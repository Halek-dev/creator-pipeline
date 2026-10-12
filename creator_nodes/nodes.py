from comfy_api.latest import io

from . import finish, loader, prompts

CATEGORY = "creator"


class CreatorCharacterPrompt(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="Creator_CharacterPrompt",
            display_name="Character Prompt",
            category=f"{CATEGORY}/prompt",
            description="Builds a prompt that always starts with your LoRA trigger word.",
            inputs=[
                io.String.Input("trigger", default="mychar", tooltip="The trigger word your LoRA was trained with."),
                io.Combo.Input(
                    "shot",
                    options=["close-up portrait", "medium shot", "full body shot", "mirror selfie", "candid from a distance"],
                ),
                io.String.Input("scene", multiline=True, default="sitting in a small cafe, soft window light"),
                io.String.Input("style", multiline=True, default="photorealistic, natural skin texture"),
            ],
            outputs=[io.String.Output(display_name="prompt")],
        )

    @classmethod
    def execute(cls, trigger, shot, scene, style) -> io.NodeOutput:
        return io.NodeOutput(prompts.build_character_prompt(trigger, shot, scene, style))


class CreatorPromptLibrary(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        libraries = prompts.list_libraries() or ["(no libraries found)"]
        return io.Schema(
            node_id="Creator_PromptLibrary",
            display_name="Prompt Library",
            category=f"{CATEGORY}/prompt",
            description="Fills a scene template from a JSON library in creator_nodes/prompt_library.",
            inputs=[
                io.Combo.Input("library", options=libraries),
                io.Combo.Input(
                    "mode",
                    options=["random", "sequential"],
                    tooltip="random: seeded pick per slot. sequential: walks every combination in order.",
                ),
                io.Int.Input("seed", default=0, min=0, max=2**31 - 1, control_after_generate=io.ControlAfterGenerate.increment),
            ],
            outputs=[
                io.String.Output(display_name="scene"),
                io.Int.Output(display_name="combinations"),
            ],
        )

    @classmethod
    def execute(cls, library, mode, seed) -> io.NodeOutput:
        lib = prompts.load_library(library)
        return io.NodeOutput(prompts.fill_template(lib, mode, seed), prompts.combination_count(lib))


class CreatorFolderLoader(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="Creator_FolderLoader",
            display_name="Folder Image Loader",
            category=f"{CATEGORY}/image",
            description="Loads one image from a folder by index. Index wraps around, so auto-increment cycles the folder.",
            inputs=[
                io.String.Input("folder", default="batch", tooltip="Absolute path, or a folder inside ComfyUI/input."),
                io.Int.Input("index", default=0, min=0, max=999999, control_after_generate=io.ControlAfterGenerate.increment),
            ],
            outputs=[
                io.Image.Output(display_name="image"),
                io.String.Output(display_name="filename"),
                io.Int.Output(display_name="count"),
            ],
        )

    @classmethod
    def _pick(cls, folder, index):
        import folder_paths

        files = loader.list_images(loader.resolve_folder(folder, folder_paths.get_input_directory()))
        return loader.pick(files, index), len(files)

    @classmethod
    def fingerprint_inputs(cls, folder, index):
        # Re-run when the chosen file changes on disk, not only when the inputs change.
        path, count = cls._pick(folder, index)
        return f"{path}|{path.stat().st_mtime_ns}|{count}"

    @classmethod
    def execute(cls, folder, index) -> io.NodeOutput:
        path, count = cls._pick(folder, index)
        return io.NodeOutput(loader.load_image_tensor(path), path.stem, count)


class CreatorRealityFinish(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="Creator_RealityFinish",
            display_name="Reality Finish",
            category=f"{CATEGORY}/image",
            description="Adds camera-like grain, vignette, warmth, lens fringing and JPEG compression.",
            inputs=[
                io.Image.Input("image"),
                io.Float.Input("grain", default=0.03, min=0.0, max=0.2, step=0.005),
                io.Float.Input("vignette", default=0.2, min=0.0, max=1.0, step=0.05),
                io.Float.Input("warmth", default=0.02, min=-0.2, max=0.2, step=0.01),
                io.Int.Input("lens_fringe_px", default=1, min=0, max=4),
                io.Int.Input("jpeg_quality", default=88, min=30, max=100),
                io.Int.Input("seed", default=0, min=0, max=2**31 - 1),
            ],
            outputs=[io.Image.Output(display_name="image")],
        )

    @classmethod
    def execute(cls, image, grain, vignette, warmth, lens_fringe_px, jpeg_quality, seed) -> io.NodeOutput:
        return io.NodeOutput(
            finish.reality_finish(image, grain, vignette, warmth, lens_fringe_px, jpeg_quality, seed)
        )


NODES = [CreatorCharacterPrompt, CreatorPromptLibrary, CreatorFolderLoader, CreatorRealityFinish]
