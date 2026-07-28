"""
main.py — Ponto de entrada do MultiA PDF Analyzer (PyWebView).
Execute: python main.py
Requisitos: pip install pywebview
"""
import sys
import os
import webview

# Garante que o pacote multia seja encontrado
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from multia.bridge import Bridge

def resource_path(rel):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)

def on_drag_drop(window, paths):
    """Callback do PyWebView para drag and drop de arquivos."""
    if paths:
        path = paths[0]
        if path.lower().endswith('.pdf'):
            bridge.pdf_path = path
            nome = os.path.basename(path)
            # Notifica o frontend via JS
            safe_nome = nome.replace("'", "\'")
            window.evaluate_js(f"carregarPDF('{safe_nome}')")
        else:
            window.evaluate_js("document.getElementById('drop-zone').innerHTML = '⚠️ Apenas arquivos PDF'")


if __name__ == "__main__":
    # Garante que prints com emojis não quebrem no Windows
    import sys, io
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    bridge = Bridge()
    html_path = resource_path(os.path.join("frontend", "index.html"))
    print(f"HTML path: {html_path}")
    print(f"Existe: {os.path.exists(html_path)}")

    window = webview.create_window(
        title       = "MultiA PDF Analyzer v3.0",
        url         = f"file:///{html_path}".replace("\\", "/"),
        js_api      = bridge,
        width       = 1100,
        height      = 820,
        min_size    = (800, 600),
        resizable   = True,
    )
    bridge._window = window

    webview.start(debug=False)
