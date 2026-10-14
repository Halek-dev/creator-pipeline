"""Look and feel of the team app: a dark, video-generation-site style built on Gradio's theme system plus a little CSS.

Only presentation lives here; app.py adds `elem_classes` hooks (hero, step, step-title, run-btn ...) and nothing else.
"""
import gradio as gr

# Palette: near-black canvas, soft cards, violet → cyan accent (the gradient of the Run button and the title).
THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.violet,
    secondary_hue=gr.themes.colors.cyan,
    neutral_hue=gr.themes.colors.zinc,
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace", "monospace"],
    radius_size=gr.themes.sizes.radius_lg,
    spacing_size=gr.themes.sizes.spacing_md,
    text_size=gr.themes.sizes.text_md,
).set(
    body_background_fill_dark="#0b0b10",
    body_text_color_dark="#e7e7ee",
    body_text_color_subdued_dark="#9a9ab0",
    background_fill_primary_dark="#13131a",
    background_fill_secondary_dark="#1a1a24",
    block_background_fill_dark="#13131a",
    block_border_color_dark="#262636",
    block_label_background_fill_dark="#1a1a24",
    block_label_text_color_dark="#9a9ab0",
    block_title_text_color_dark="#c9c9d8",
    input_background_fill_dark="#0f0f16",
    input_border_color_dark="#2b2b3c",
    input_border_color_focus_dark="#8b5cf6",
    border_color_primary_dark="#262636",
    button_primary_background_fill_dark="linear-gradient(90deg, #7c3aed 0%, #06b6d4 100%)",
    button_primary_background_fill_hover_dark="linear-gradient(90deg, #8b5cf6 0%, #22d3ee 100%)",
    button_primary_text_color_dark="#ffffff",
    button_primary_border_color_dark="transparent",
    button_secondary_background_fill_dark="#1e1e2a",
    button_secondary_background_fill_hover_dark="#2a2a3a",
    button_secondary_text_color_dark="#e7e7ee",
    button_secondary_border_color_dark="#2b2b3c",
    checkbox_background_color_selected_dark="#7c3aed",
    checkbox_border_color_selected_dark="#7c3aed",
    slider_color_dark="#7c3aed",
    table_border_color_dark="#262636",
    table_even_background_fill_dark="#13131a",
    table_odd_background_fill_dark="#171720",
    table_row_focus_dark="#1f1f2e",
    shadow_drop="0 1px 2px rgba(0,0,0,.4)",
    shadow_drop_lg="0 10px 30px rgba(0,0,0,.45)",
    block_shadow_dark="0 1px 0 rgba(255,255,255,.03) inset, 0 8px 24px rgba(0,0,0,.35)",
)

CSS = """
/* ---------- page ---------- */
.gradio-container { max-width: 1320px !important; margin: 0 auto !important; }
body.dark { background:
  radial-gradient(900px 420px at 10% -10%, rgba(124,58,237,.22), transparent 60%),
  radial-gradient(800px 380px at 95% 0%, rgba(6,182,212,.16), transparent 60%),
  #0b0b10 !important; }
footer { display: none !important; }

/* ---------- hero ---------- */
.hero { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; padding: 18px 4px 6px; flex-wrap: wrap; }
.hero .brand { display: flex; align-items: center; gap: 12px; }
.hero .logo { width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; color: #fff; font-weight: 800;
  background: linear-gradient(135deg, #7c3aed, #06b6d4); box-shadow: 0 8px 24px rgba(124,58,237,.35); }
.hero h1 { margin: 0; font-size: 26px; font-weight: 750; letter-spacing: -.02em; line-height: 1.1;
  background: linear-gradient(90deg, #fff 0%, #c4b5fd 55%, #67e8f9 100%); -webkit-background-clip: text; background-clip: text; color: transparent; }
.hero .tag { margin: 2px 0 0; color: #9a9ab0; font-size: 13px; }
.status-chip { display: inline-flex; align-items: center; gap: 8px; padding: 7px 12px; border-radius: 999px; font-size: 12.5px;
  color: #c9c9d8; background: rgba(255,255,255,.04); border: 1px solid #262636; }
.status-chip::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: #22c55e; box-shadow: 0 0 10px #22c55e; }
.status-chip.busy::before { background: #f59e0b; box-shadow: 0 0 10px #f59e0b; }

/* ---------- tabs as pills ---------- */
.tabs > .tab-nav, .tabs .tab-wrapper .tab-container { border-bottom: 0 !important; gap: 6px; padding: 8px 0 14px; }
.tabs > .tab-nav > button, .tabs .tab-container > button { border: 1px solid transparent !important; border-radius: 999px !important; padding: 8px 16px !important;
  color: #9a9ab0 !important; font-weight: 600; background: transparent; }
.tabs > .tab-nav > button.selected, .tabs .tab-container > button.selected { color: #fff !important; background: rgba(124,58,237,.18) !important; border-color: rgba(124,58,237,.5) !important; }

/* ---------- step cards ---------- */
.step { padding: 16px 16px 10px !important; border: 1px solid #262636 !important; border-radius: 16px !important;
  background: linear-gradient(180deg, rgba(255,255,255,.025), rgba(255,255,255,0)) #13131a !important; margin-bottom: 14px; }
.step-title { display: flex; align-items: center; gap: 10px; margin: 0 0 8px; }
.step-title .n { width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center; font-size: 12px; font-weight: 800; color: #fff;
  background: linear-gradient(135deg, #7c3aed, #06b6d4); }
.step-title h3 { margin: 0; font-size: 15px; font-weight: 700; color: #e7e7ee; letter-spacing: -.01em; }
.step-title span.hint { color: #9a9ab0; font-size: 12.5px; margin-left: auto; }

/* ---------- inputs ---------- */
.upload-tile .image-container, .upload-tile .video-container, .upload-tile .wrap { border-radius: 14px !important; }
.upload-tile .wrap.default, .upload-tile .upload-container { border: 1.5px dashed #34344a !important; border-radius: 14px !important; background: #0f0f16 !important; }
.upload-tile .wrap:hover { border-color: #8b5cf6 !important; }
.preflight { font-size: 13.5px; line-height: 1.55; color: #c9c9d8; }
.estimate { font-size: 13.5px; color: #c4b5fd; padding: 10px 12px; margin-top: 6px; border-radius: 10px;
  background: rgba(124,58,237,.10) !important; border: 1px solid rgba(124,58,237,.35); }
.preflight { padding: 6px 2px 0; }
.step .prose, .step .md { background: transparent !important; }

/* radio groups as segmented controls */
fieldset.wrap > label { border-radius: 999px !important; }

/* ---------- run button ---------- */
.run-btn { min-height: 52px !important; font-size: 16px !important; font-weight: 750 !important; letter-spacing: .01em; border-radius: 14px !important;
  box-shadow: 0 10px 30px rgba(124,58,237,.35) !important; }
.run-btn:hover { transform: translateY(-1px); }

/* ---------- jobs ---------- */
.jobs-table table { font-size: 13px; }
.jobs-table th { text-transform: uppercase; letter-spacing: .06em; font-size: 11px; color: #9a9ab0 !important; }
.detail { font-size: 13.5px; line-height: 1.6; }
.detail code { background: #1a1a24; color: #c4b5fd; padding: 1px 6px; border-radius: 6px; }

/* ---------- help ---------- */
.help { max-width: 860px; line-height: 1.65; font-size: 14px; }
.help h3 { margin-top: 1.2em; color: #c4b5fd; }
"""

# Forces the dark look regardless of the browser's preference: the app is designed dark only.
FORCE_DARK_JS = """
() => {
  document.documentElement.classList.add('dark');
  document.body.classList.add('dark');
  if (!window.location.search.includes('__theme=dark')) {
    const url = new URL(window.location); url.searchParams.set('__theme', 'dark'); window.history.replaceState({}, '', url);
  }
}
"""
