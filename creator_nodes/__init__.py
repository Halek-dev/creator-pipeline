"""Creator node pack for ComfyUI."""
from comfy_api.latest import ComfyExtension, io

from .nodes import NODES


class CreatorExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return NODES


async def comfy_entrypoint() -> CreatorExtension:
    return CreatorExtension()
