"""Resolve complete SC2 virtual paths in active component order; export web images."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import subprocess

from PIL import Image, ImageStat


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AssetBuilder:
    def __init__(self, output, toolbox_root=None, game_root=None):
        self.output = Path(output)
        self.directory = self.output / "assets" / "images"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.toolbox_root = Path(toolbox_root) if toolbox_root else None
        self.game_root = Path(game_root) if game_root else None
        self.cache = {}
        self.records = []
        self.lookups = {}
        self.model_cache = {}

    @staticmethod
    def roots(data):
        roots = data.get("project", {}).get("component_roots", [])
        if isinstance(roots, dict):
            roots = list(roots.values())
        result = []
        for root in roots:
            if isinstance(root, dict):
                root = root.get("root") or root.get("path") or root.get("component_dir")
            if root:
                result.append(Path(root))
        return result

    def resolve(self, virtual, roots):
        virtual = str(virtual).replace("\\", "/")
        pure = PurePosixPath(virtual)
        if pure.is_absolute() or any(p in ("..", ".") for p in pure.parts) or ":" in virtual or "##" in virtual:
            return None
        for root in roots:
            # Root and component storage prefixes are ordered; never match basenames.
            for prefix in ("", "Base.SC2Assets", "base.sc2assets", "Base.SC2Data", "base.sc2data"):
                base = root / prefix
                direct = base.joinpath(*pure.parts)
                if direct.is_file():
                    return direct
                current = base
                for part in pure.parts:
                    key = str(current)
                    if key not in self.lookups:
                        self.lookups[key] = {p.name.lower(): p for p in current.iterdir()} if current.is_dir() else {}
                    current = self.lookups[key].get(part.lower())
                    if current is None:
                        break
                if current is not None and current.is_file():
                    return current
        return None

    def image(self, fact, data, role="icon", m3_fallback=None):
        virtual = fact.get("value") if isinstance(fact, dict) else fact
        roots = self.roots(data)
        key = (str(virtual), tuple(map(str, roots)), role, str(m3_fallback.get("value")) if isinstance(m3_fallback, dict) else '')
        if key in self.cache:
            return self.cache[key]
        path = self.resolve(virtual, roots) if virtual else None
        record = {"virtual_path": virtual, "role": role, "consumer_component": data.get('project',{}).get('primary_mod'), "evidence": fact.get("evidence", []) if isinstance(fact, dict) else [], "status": "missing"}
        result = None
        if path:
            try:
                sha = digest(path)
                filename = sha[:24] + '-' + role + ".png"
                image = Image.open(path).convert("RGBA")
                image.thumbnail((512, 512) if role == "portrait" else (160, 160), Image.Resampling.LANCZOS)
                target = self.directory / filename
                if not target.exists():
                    image.save(target)
                result = "assets/images/" + filename
                record.update(status="resolved", source=str(path), source_sha256=sha, output=result, output_sha256=digest(target), method="DDS/image to PNG via Pillow")
            except (OSError, ValueError) as error:
                record["reason"] = str(error)
        if result is None and role == "portrait" and isinstance(m3_fallback, dict) and m3_fallback.get("status") == "confirmed":
            result, model_record = self.model_preview(m3_fallback, roots)
            record.update(model_record)
            record['model_evidence'] = m3_fallback.get('evidence',[])
        if result is None:
            record.setdefault("reason", "活动组件中未找到完整路径对应的可转换资产；使用明确占位")
        self.cache[key] = result
        self.records.append(record)
        return result

    def model_preview(self, fact, roots):
        model = self.resolve(fact.get("value"), roots)
        exe = self.toolbox_root / "tools" / "m3viewer" / "m3viewer.exe" if self.toolbox_root else None
        if not model or not exe or not exe.is_file():
            return None, {"reason": "无可用具体 M3 或静态预览工具"}
        sha = digest(model)
        model_key=(str(model.resolve()),sha)
        if model_key in self.model_cache:
            return self.model_cache[model_key]
        preview_key=hashlib.sha256((str(model.resolve())+sha).encode('utf-8')).hexdigest()[:24]
        target = self.directory / (preview_key + "-m3.png")
        ppm = self.directory / (preview_key + "-m3.ppm")
        try:
            environment = dict(os.environ)
            if self.game_root:
                environment['SC2PATH'] = str(self.game_root)
            completed = subprocess.run([str(exe), "--model", str(model), "--selftest", str(ppm)], cwd=self.directory, env=environment, capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode or not ppm.exists():
                self.model_cache[model_key] = (None, {"reason": "M3 静态预览未成功", "returncode": completed.returncode})
                return self.model_cache[model_key]
            logs = completed.stdout.decode('utf-8',errors='replace') + completed.stdout.decode('gb18030',errors='replace')
            hit = re.search(r'材质贴图[^\n]*?命中\s+(\d+)',logs)
            texture_hits = int(hit.group(1)) if hit else None
            missed = re.search(r'未命中\s+(\d+)',logs)
            texture_misses = int(missed.group(1)) if missed else None
            with Image.open(ppm) as image:
                rgb = image.convert("RGB")
                stats = ImageStat.Stat(rgb)
                visible = max(stats.extrema[i][1] for i in range(3)) > 8 and max(stats.stddev) > 2
                if not visible or not texture_hits or texture_misses != 0 or '降级为棋盘格' in logs:
                    self.model_cache[model_key] = (None, {"reason": "M3 预览黑屏/无可见模型或贴图未完整命中，避免使用棋盘格降级肖像", "texture_hits":texture_hits, "texture_misses":texture_misses, "source":str(model), "source_sha256":sha})
                    return self.model_cache[model_key]
                rgb.save(target)
            ppm.unlink()
            result = "assets/images/" + target.name
            self.model_cache[model_key] = (result, {"status": "resolved", "virtual_path": fact["value"], "source": str(model), "source_sha256": sha, "output": result, "output_sha256": digest(target), "method": "m3viewer --selftest static preview", "texture_hits":texture_hits, "texture_misses":texture_misses})
            return self.model_cache[model_key]
        except (OSError, subprocess.TimeoutExpired) as error:
            self.model_cache[model_key] = (None, {"reason": "M3 静态预览失败: " + str(error)})
            return self.model_cache[model_key]
        finally:
            if ppm.exists():
                ppm.unlink()

    def save(self):
        path = self.output / "assets" / "manifest.json"
        path.write_text(json.dumps({"version": 1, "assets": self.records}, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
