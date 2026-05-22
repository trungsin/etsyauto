"""Template registry — discovers and loads templates from a directory tree.

Expected layout: {root}/{product}/{slug}/base.png + meta.json (+ optional mask.png)
"""
import json
from dataclasses import dataclass
from pathlib import Path

from .template_meta import TemplateMeta


class TemplateLoadError(RuntimeError):
    def __init__(self, path: Path, reason: str):
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class Template:
    id: str
    root: Path
    base_path: Path
    mask_path: Path | None
    meta: TemplateMeta


class TemplateRegistry:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.templates: dict[str, Template] = {}
        self.errors: list[TemplateLoadError] = []
        self.reload()

    def reload(self) -> None:
        self.templates.clear()
        self.errors.clear()
        if not self.root.exists():
            return
        for meta_path in self.root.glob("*/*/meta.json"):
            try:
                template = self._load_one(meta_path)
                self.templates[template.id] = template
            except TemplateLoadError as exc:
                self.errors.append(exc)

    def _load_one(self, meta_path: Path) -> Template:
        try:
            meta = TemplateMeta.model_validate(json.loads(meta_path.read_text()))
        except Exception as exc:
            raise TemplateLoadError(meta_path, str(exc)) from exc
        template_dir = meta_path.parent
        base_path = template_dir / "base.png"
        if not base_path.exists():
            raise TemplateLoadError(meta_path, "missing base.png")
        mask_path = template_dir / meta.mask if meta.mask else None
        if mask_path and not mask_path.exists():
            raise TemplateLoadError(meta_path, f"missing mask file {meta.mask}")
        return Template(str(template_dir.relative_to(self.root)), template_dir, base_path, mask_path, meta)

    def list_products(self) -> list[str]:
        return sorted({item.meta.product for item in self.templates.values()})

    def find(
        self,
        product: str,
        angle: str | None = None,
        color: str | None = None,
        scene_type: str | None = None,
    ) -> list[Template]:
        results = []
        for template in self.templates.values():
            meta = template.meta
            if meta.product != product:
                continue
            if angle and meta.angle != angle:
                continue
            if color and meta.color != color:
                continue
            if scene_type and meta.scene_type != scene_type:
                continue
            results.append(template)
        return sorted(results, key=lambda t: t.id)
