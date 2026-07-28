"""
fotos.py — Extração de fotos e QR codes de PDFs de laudos.
extrair_fotos_pdf: extração direta por posição (laudo normal).
extrair_fotos_qrcode_pdf: extração via QR codes (laudo antigo).
obter_sessao_autenticada: login no frontend infoel para download de imagens.
"""
import re, requests, base64
from requests.exceptions import SSLError
from .config import AUTH_BASIC, AUTH_BASIC_MAIS
from .utils import _normalizar_nome_foto, _titulo_valido

def _print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))

_SECAO_FOTOS = "FOTOS DO IMÓVEL TIRADAS PELO TÉCNICO NO LOCAL"
# Títulos que NÃO são nomes de foto (logos, sumário, seções técnicas, etc.)
_IGNORAR_TITULOS = {
    "multia avaliações","multiavaliações","multi a avaliações",
    "sumário","finalidade","metodologia","ressalvas","objeto do parecer",
    "localização","espelho cadastral","sistema de informações",
    "vistoria do imóvel","características do imóvel","risco socioambiental",
    "considerações finais","memorial de cálculo","valor do imóvel",
    "informações do avaliador","informações da empresa","legenda",
    "google earth","google","fonte","obs.","obs:","continua no verso",
}

def _titulo_valido(txt):
    """Retorna True se o texto parece ser um título de foto."""
    t = txt.strip()
    if not t or len(t) < 3 or len(t) > 120: return False
    tl = t.lower()
    if any(ig in tl for ig in _IGNORAR_TITULOS): return False
    # Deve conter pelo menos uma letra
    if not re.search(r"[A-Za-zÀ-ÿ]", t): return False
    return True

def extrair_fotos_pdf(pdf_path):
    """
    Extrai fotos da seção de FOTOS DO IMÓVEL.
    Associa cada imagem ao título mais próximo na mesma coluna (acima ou bordas sobrepostas).
    """
    fotos = []
    try:
        import fitz
        doc = fitz.open(pdf_path)

        # ── 1. Detectar páginas da seção de fotos ──
        _PAT_FOTOS = re.compile(r"\d+[\-–—\s]*FOTOS?\s+DO\s+IM", re.IGNORECASE)
        pagina_inicio = pagina_fim = None
        _PAT_PROX = None
        n_total = len(doc)
        # Pular sumário: nas 10 primeiras páginas só se o doc tiver mais de 15 páginas
        _skip = 10 if n_total > 15 else 3
        for i, page in enumerate(doc):
            if i < _skip: continue
            txt = page.get_text()
            if pagina_inicio is None:
                m = _PAT_FOTOS.search(txt)
                if m:
                    pagina_inicio = i
                    nm = re.search(r"(\d+)", m.group())
                    n = int(nm.group(1)) if nm else 12
                    _PAT_PROX = re.compile(r"^\s*" + str(n+1) + r"[\-–—\s]", re.MULTILINE)
            elif _PAT_PROX and _PAT_PROX.search(txt):
                pagina_fim = i; break

        if pagina_inicio is None: pagina_inicio = _skip
        if pagina_fim is None: pagina_fim = len(doc)
        _print(f"[fotos] Seção: pág {pagina_inicio+1}–{pagina_fim} de {len(doc)}")

        # ── Marcas que indicam imagem de mapa/satélite ──
        _MARCAS_MAPA = ("google earth","google","sicar","cadastro ambiental",
                        "sistema nacional","croqui","mat.","matrícula","-30.","-29.","-28.")

        xrefs_vistos = set()

        for page in doc[pagina_inicio:pagina_fim]:
            imgs = page.get_images(full=True)
            if not imgs: continue

            page_w, page_h = page.rect.width, page.rect.height
            page_rot = page.rotation  # 0, 90, 180 ou 270

            # Coletar todos os blocos de texto com bbox completo
            blocos = page.get_text("dict")["blocks"]
            textos = []  # (x0, y0, x1, y1, txt)
            for b in blocos:
                if b["type"] != 0: continue
                for linha in b["lines"]:
                    t = " ".join(s["text"] for s in linha["spans"]).strip()
                    if t:
                        bx = b["bbox"]
                        textos.append((bx[0], bx[1], bx[2], bx[3], t))

            # ── Passo 1: coletar fatias candidatas (coordenadas PDF brutas) ──
            # NÃO transformamos coordenadas — usamos PDF bruto e adaptamos
            # a lógica de agrupamento conforme page_rot.
            #
            # page_rot=0:   fatias da mesma foto têm mesmo r.y0  → agrupar por y0, colar horizontal
            # page_rot=90:  fatias da mesma foto têm mesmo r.x0  → agrupar por x0, colar vertical
            # page_rot=180: fatias da mesma foto têm mesmo r.y0  → agrupar por y0, colar horizontal (invertido)
            # page_rot=270: fatias da mesma foto têm mesmo r.x0  → agrupar por x0, colar vertical (invertido)

            # Para filtros de cabeçalho e largura mínima, usamos dimensões corretas
            # conforme a rotação:
            # rot=0/180: cabeçalho = y0/page_h < 0.10; estreita = width/page_w < 0.08
            # rot=90/270: cabeçalho = x0/page_w < 0.10; estreita = height/page_h < 0.08

            rot_lateral = page_rot in (90, 270)

            fatias_candidatas = []
            for img_info in imgs:
                xref = img_info[0]
                if xref in xrefs_vistos: continue

                bi = doc.extract_image(xref)
                w, h = bi.get("width", 0), bi.get("height", 0)

                # ── Filtro de tamanho ──
                # PDFs podem armazenar fotos como tiras horizontais muito baixas (ex: 992×88px)
                # empilhadas verticalmente para compor a foto completa (padrão MultiA).
                # Por isso só exigimos que UMA das dimensões seja >= 200px,
                # e que a largura seja >= 100px (elimina ícones/separadores muito estreitos).
                if max(w, h) < 200 or w < 100:
                    _print(f"  [fotos] ✗ xref={xref} tamanho pequeno: {w}x{h}"); continue

                # Ratio: tiras horizontais têm ratio altíssimo (992/88 ≈ 11).
                # Não filtramos por ratio aqui — o filtro de largura relativa à página
                # e o filtro de cabeçalho são suficientes para excluir lixo.

                rects = page.get_image_rects(xref)
                if not rects: continue
                r = rects[0]

                # ── Filtro cabeçalho: logo/marca no topo da página ──
                if page_rot == 0 or page_rot == 180:
                    cab_ratio = r.y0 / page_h
                elif page_rot == 90:
                    cab_ratio = (page_w - r.x1) / page_w
                else:  # 270
                    cab_ratio = r.x0 / page_w
                if cab_ratio < 0.05:
                    _print(f"  [fotos] ✗ xref={xref} cabeçalho: {cab_ratio:.2f}"); continue

                # ── Filtro de largura mínima no PDF ──
                # Imagens muito estreitas no espaço PDF (< 8% da largura) são descartadas.
                # Tiras MultiA (~992px de pixel) ocupam ~45% da largura do PDF → passam.
                # Separadores/bordas de tabela são estreitíssimos → bloqueados.
                if not rot_lateral:
                    slim_ratio = r.width / page_w
                    if slim_ratio < 0.08:
                        _print(f"  [fotos] ✗ xref={xref} estreita: {slim_ratio:.2f} ({w}x{h})"); continue

                margem = 80
                vizinhos_txt = " ".join(
                    t for x0,y0,x1,y1,t in textos
                    if not (x1 < r.x0-margem or x0 > r.x1+margem or
                            y1 < r.y0-margem or y0 > r.y1+margem)
                ).lower()
                if any(m in vizinhos_txt for m in _MARCAS_MAPA):
                    _print(f"  [fotos] ✗ xref={xref} mapa: {vizinhos_txt[:60]!r}")
                    continue

                # ── Chaves de agrupamento ──
                # Dois padrões de fatiamento existem neste código:
                #
                # A) FATIAS HORIZONTAIS (rot=0, fatiamento lateral):
                #    Mesma foto dividida em colunas estreitas (x0 varia, y0 igual).
                #    → agrupar por y0 (estável), ordenar por x0 (varia).
                #    → montagem: colar lado a lado (horizontal).
                #
                # B) TIRAS VERTICAIS (rot=0, padrão MultiA):
                #    Mesma foto dividida em linhas baixas (~88px altura) empilhadas.
                #    Cada tira: largura ~45% da página, altura ~88px.
                #    Tiras da MESMA foto têm mesmo x0 e x1 (mesma coluna).
                #    Tiras de fotos DIFERENTES têm x0 diferente (colunas diferentes).
                #    → agrupar por x0 (estável), ordenar por y0 (varia).
                #    → montagem: colar de cima para baixo (vertical).
                #
                # Detectamos o padrão B quando h_pixel < 200 (tira baixa).
                eh_tira_vertical = (h < 200 and w >= 200)

                if eh_tira_vertical:
                    grupo_key = round(r.x0)   # x0 é estável entre tiras da mesma foto
                    ordem_key = r.y0           # y0 varia (posição vertical da tira)
                else:
                    grupo_key = r.y0           # padrão A e fotos completas
                    ordem_key = r.x0

                fatias_candidatas.append((grupo_key, ordem_key, r.x0, r.y0, r.x1, r.y1, xref, bi, eh_tira_vertical))

            if not fatias_candidatas:
                continue

            # ── Passo 2: agrupar fatias/tiras pelo eixo estável ──
            #
            # Três casos:
            # 1. Tiras verticais (padrão MultiA): agrupam por x0 próximo (tolerância 5px),
            #    sem limite de largura — qualquer tira estreita na mesma coluna é do mesmo grupo.
            #
            # 2. Fatias horizontais estreitas (< 35% da largura): agrupam por y0 próximo.
            #
            # 3. Fotos completas (>= 35% da largura, h >= 200): grupo próprio sempre.
            #
            LIMIAR_FOTO_COMPLETA = 0.35  # >= 35% da largura = foto completa, não agrupa com outras

            # Separar candidatas por tipo para processar corretamente
            tiras_vert    = [f for f in fatias_candidatas if f[8]]      # eh_tira_vertical=True
            demais        = [f for f in fatias_candidatas if not f[8]]  # fotos completas ou fatias horiz

            grupos = []

            # ── Agrupar tiras verticais por x0 (coluna) + contiguidade em y ──
            # Tiras da MESMA foto: mesmo x0, y0 consecutivos (gap ≤ MARGEM_TIRA).
            # Tiras de fotos DIFERENTES: mesmo x0, mas gap > MARGEM_TIRA (há label/espaço entre fotos).
            MARGEM_TIRA = 10  # pt — gap máximo entre tiras contíguas da mesma foto
            tiras_vert.sort(key=lambda f: (f[0], f[1]))  # ordena por (x0_arredondado, y0)
            for tira in tiras_vert:
                key_x0 = tira[0]   # x0 arredondado
                tira_y0 = tira[3]  # y0 real da tira
                agrupado = False
                for grupo in grupos:
                    if not grupo[0][8]: continue  # não misturar com outros tipos
                    if abs(key_x0 - grupo[0][0]) >= 5: continue  # coluna diferente
                    ultima_y1 = max(f[5] for f in grupo)  # y1 da tira mais baixa do grupo
                    gap = tira_y0 - ultima_y1
                    if gap <= MARGEM_TIRA:  # contígua (gap ≈ 0 a MARGEM_TIRA pt)
                        grupo.append(tira)
                        agrupado = True
                        break
                if not agrupado:
                    grupos.append([tira])

            # ── Agrupar fotos completas e fatias horizontais ──
            demais.sort(key=lambda f: (round(f[0]), f[1]))
            for fatia in demais:
                key_f  = fatia[0]   # r.y0
                f_larg = (fatia[4] - fatia[2]) / page_w

                agrupado = False
                if f_larg < LIMIAR_FOTO_COMPLETA:
                    for grupo in grupos:
                        if grupo[0][8]: continue  # não misturar com grupos de tiras verticais
                        g_larg = (grupo[0][4] - grupo[0][2]) / page_w
                        if g_larg >= LIMIAR_FOTO_COMPLETA: continue
                        if abs(key_f - grupo[0][0]) < 2:
                            grupo.append(fatia)
                            agrupado = True
                            break
                if not agrupado:
                    grupos.append([fatia])

            # ── Passo 3: para cada grupo, montar a foto final ──
            for grupo in grupos:
                eh_tiras = grupo[0][8]  # True = tiras verticais (padrão MultiA)
                # tiras verticais → ordenar por y0 (de cima para baixo)
                # demais → ordenar por x0 (esq para dir)
                grupo.sort(key=lambda f: f[1])

                xref_rep = grupo[0][6]
                if xref_rep in xrefs_vistos:
                    continue
                for f in grupo:
                    xrefs_vistos.add(f[6])

                # bbox do grupo todo (para busca de título)
                r_x0 = min(f[2] for f in grupo)
                r_y0 = min(f[3] for f in grupo)
                r_x1 = max(f[4] for f in grupo)
                r_y1 = max(f[5] for f in grupo)

                # ── Título: texto imediatamente acima (espaço PDF) ──
                candidatos = [
                    (y1, t) for x0,y0,x1,y1,t in textos
                    if y1 <= r_y0 + 30
                    and y1 >= r_y0 - 120
                    and x0 < r_x1
                    and x1 > r_x0
                    and _titulo_valido(t)
                ]
                candidatos.sort(key=lambda x: x[0], reverse=True)
                nome = candidatos[0][1].strip() if candidatos else None

                if not nome:
                    candidatos2 = [
                        (y1, t) for x0,y0,x1,y1,t in textos
                        if y1 <= r_y0 + 5
                        and y1 >= r_y0 - 200
                        and x0 < r_x1 and x1 > r_x0
                        and _titulo_valido(t)
                    ]
                    candidatos2.sort(key=lambda x: x[0], reverse=True)
                    nome = candidatos2[0][1].strip() if candidatos2 else None

                nome_foto = _normalizar_nome_foto(nome)

                # ── Montar imagem final ──
                try:
                    from PIL import Image as _PilImg
                    import io as _io

                    fatias_pil = [_PilImg.open(_io.BytesIO(f[7]["image"])) for f in grupo]

                    if len(fatias_pil) == 1:
                        img_final = fatias_pil[0]
                        img_ext = grupo[0][7]["ext"]
                    elif eh_tiras:
                        # Tiras verticais (padrão MultiA): empilhar de cima para baixo
                        max_w   = max(p.width  for p in fatias_pil)
                        total_h = sum(p.height for p in fatias_pil)
                        img_final = _PilImg.new("RGB", (max_w, total_h))
                        y_off = 0
                        for p in fatias_pil:
                            img_final.paste(p, (0, y_off))
                            y_off += p.height
                        _print(f"  [fotos] 🧩 {len(grupo)} tiras vert → {max_w}x{total_h}")
                        img_ext = "jpg"
                    elif rot_lateral:
                        # rot=90: fatias têm mesmo y0, x0 varia (esq→dir no PDF)
                        # rotate(-90°) transforma: PDF-x → visual-y (de cima p/ baixo)
                        #                          PDF-y → visual-x (esq p/ dir)
                        # Para que as fatias fiquem lado a lado na visual,
                        # precisam estar empilhadas verticalmente no PDF (eixo Y).
                        # Ordenar por x0 crescente = ordem visual esq→dir = empilhar de cima p/ baixo
                        total_h = sum(p.height for p in fatias_pil)
                        max_w   = max(p.width  for p in fatias_pil)
                        img_final = _PilImg.new("RGB", (max_w, total_h))
                        y_off = 0
                        for p in fatias_pil:
                            img_final.paste(p, (0, y_off))
                            y_off += p.height
                        _print(f"  [fotos] 🧩 {len(grupo)} fatias vert → {max_w}x{total_h}")
                        img_ext = "jpg"
                    else:
                        # rot=0/180: fatias lado a lado no eixo X
                        total_w = sum(p.width  for p in fatias_pil)
                        max_h   = max(p.height for p in fatias_pil)
                        img_final = _PilImg.new("RGB", (total_w, max_h))
                        x_off = 0
                        for p in fatias_pil:
                            img_final.paste(p, (x_off, 0))
                            x_off += p.width
                        _print(f"  [fotos] 🧩 {len(grupo)} fatias horiz → {total_w}x{max_h}")
                        img_ext = "jpg"

                    if page_rot != 0:
                        img_final = img_final.rotate(-page_rot, expand=True)
                        _print(f"  [fotos] ↻ rotacionado {page_rot}°")

                    buf = _io.BytesIO()
                    img_final.save(buf, format="JPEG")
                    img_bytes = buf.getvalue()
                    img_ext   = "jpg"

                except Exception as ex_pil:
                    _print(f"  [fotos] ⚠ PIL falhou ({ex_pil}), usando raw")
                    img_bytes = grupo[0][7]["image"]
                    img_ext   = grupo[0][7]["ext"]

                fotos.append({"bytes": img_bytes, "ext": img_ext, "nome": nome_foto})
                _print(f"  [fotos] ✔ xref={xref_rep} grupo={len(grupo)} pág={page.number+1} nome='{nome_foto}'")
                _print(f"  [fotos] ✔ xref={xref_rep} grupo={len(grupo)} pág={page.number+1} nome='{nome_foto}'")

        doc.close()
    except Exception as ex:
        _print(f"Aviso fotos: {ex}")
        import traceback; traceback.print_exc()
    return fotos


def extrair_fotos_qrcode_pdf(pdf_path, jwt_headers=None, log_fn=None, sistema="multia", download_session=None):
    """
    Extrai fotos de laudos antigos que usam QR codes nas miniaturas.
    Associa cada QR ao nome de cômodo mais próximo acima, baixa as imagens.
    """
    def _log(msg):
        _print(msg)
        if log_fn: log_fn(msg)

    fotos = []
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        _log(f"[qr] ❌ opencv não instalado: {e} — rode: pip install opencv-python")
        return fotos
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        _log(f"[qr] ❌ pypdfium2 não instalado: {e} — rode: pip install pypdfium2")
        return fotos
    try:
        import pdfplumber
    except ImportError as e:
        _log(f"[qr] ❌ pdfplumber não instalado: {e} — rode: pip install pdfplumber")
        return fotos

    _IGNORAR_NOMES = {
        "rua","quinze","novembro","joinville","cep","multiavaliação","tecnologia",
        "avaliações","serviços","ltda","fotos","imóvel","tiradas","pelo","técnico",
        "local","do","de","no","e","em","na","com","para","das","dos","da","a","o",
        "fonte","obs","continua","verso","página","pág"
    }

    def _nome_valido(t):
        t = t.strip()
        if len(t) < 3 or len(t) > 60: return False
        if t.lower() in _IGNORAR_NOMES: return False
        if re.match(r"^[\d\W]+$", t): return False
        return True

    try:
        pdf_pdfium = pdfium.PdfDocument(pdf_path)
        detector = cv2.QRCodeDetector()
        SCALE = 2.5
        _STOP_WORDS = ["MEMORIAL DE CÁLCULO","VALOR DO IMÓVEL","INFORMAÇÕES DO AVALIADOR",
                       "INFORMAÇÕES DA EMPRESA","COMPARATIVOS","HOMOGENEIZAÇÃO"]

        # Lista de (url, nome) mantendo ordem do documento
        resultados = []  # [(url, nome)]
        urls_set = set()

        n_pags = len(pdf_pdfium)
        _log(f"[qr] Escaneando {n_pags} páginas...")

        with pdfplumber.open(pdf_path) as plumb:
            for i in range(10, n_pags):
                try:
                    p_plumb = plumb.pages[i]
                    txt_page = (p_plumb.extract_text() or "").upper()
                except Exception:
                    txt_page = ""

                if any(x in txt_page for x in _STOP_WORDS):
                    _log(f"[qr] Página {i+1}: seção técnica — parando")
                    break

                try:
                    page_obj = pdf_pdfium[i]
                    bitmap = page_obj.render(scale=SCALE)
                    pil_img = bitmap.to_pil()
                    img_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

                    retval, decoded_info, points, _ = detector.detectAndDecodeMulti(img_bgr)
                    if not retval or not decoded_info:
                        continue

                    # Pegar palavras com posição da página
                    try:
                        words = p_plumb.extract_words()
                    except Exception:
                        words = []

                    novos = 0
                    for info, pts in zip(decoded_info, points):
                        if not info or not info.startswith("http") or info in urls_set:
                            continue
                        urls_set.add(info)

                        # Posição do QR em coords da página
                        qr_cx = pts[:, 0].mean() / SCALE
                        qr_cy = pts[:, 1].mean() / SCALE

                        # Nome: palavra mais próxima acima na mesma coluna
                        candidatos = []
                        for w in words:
                            wy = w["top"]
                            if wy < qr_cy and wy > qr_cy - 280:
                                if w["x0"] < qr_cx + 90 and w["x1"] > qr_cx - 90:
                                    if _nome_valido(w["text"]):
                                        candidatos.append((wy, w["text"]))
                        if candidatos:
                            candidatos.sort(key=lambda x: x[0], reverse=True)
                            nome = _normalizar_nome_foto(candidatos[0][1])
                        else:
                            nome = "Foto"

                        resultados.append((info, nome))
                        novos += 1

                    if novos:
                        _log(f"[qr] Página {i+1}: {novos} QR(s) (total: {len(resultados)})")
                except Exception as ex_pag:
                    _log(f"[qr] ⚠️ Erro na página {i+1}: {ex_pag}")

        _log(f"[qr] {len(resultados)} foto(s) encontradas")
        if not resultados:
            return fotos

        # Ordenar por número no nome do arquivo para sequência correta
        def _sort_key(item):
            m = re.search(r"_(\d+)\.", item[0])
            return int(m.group(1)) if m else 0
        resultados.sort(key=_sort_key)

        # Headers para download
        base_url = f"https://{sistema}.infoel.info"
        hdrs_base = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/jpeg,image/*,*/*",
            "Origin": base_url,
            "Referer": base_url + "/",
        }
        if jwt_headers:
            for k in ("authorization", "jwt", "Authorization", "JWT"):
                if k in jwt_headers:
                    hdrs_base[k] = jwt_headers[k]

        # ── Estratégia: usar a API para obter lista de imagens da vistoria ──
        # O JSON de buscardadosvistoriaimovel/{uuid} tem dados.imagens[].NOMEARQUIVO
        # A URL de download é: https://{sistema}.infoel.info/arquivo/{uuid}/{NOMEARQUIVO}
        # O servidor PHP autentica por cookie SESSAO (já está na download_session)
        # IMPORTANTE: não enviar headers da API (authorization/jwt) no download do frontend

        # ── Extrair imagens diretamente do PDF ──
        # As fotos estão embedadas no PDF — o QR code é só referência ao servidor.
        # Usamos o QR para obter o nome do cômodo e o número do arquivo,
        # e extraímos os bytes diretamente do PDF com fitz.
        _log(f"[qr] 📂 Extraindo imagens diretamente do PDF...")
        try:
            import fitz as _fitz
            _doc = _fitz.open(pdf_path)

            # Mapa: número do arquivo (ex: '005') → nome do cômodo dos QR codes
            _mapa_num_nome = {}
            for url_qr, nome_qr in resultados:
                m_num = re.search(r"_(\d+)\.", url_qr)
                if m_num:
                    _mapa_num_nome[m_num.group(1).lstrip("0") or "0"] = nome_qr
                    _mapa_num_nome[m_num.group(1)] = nome_qr  # com zeros também

            # Detectar páginas da seção de fotos
            _PAT_FOTOS_QR = re.compile(r"FOTOS\s+DO\s+IM", re.IGNORECASE)
            _PAT_STOP = re.compile(r"MEMORIAL\s+DE\s+CÁLCULO|VALOR\s+DO\s+IMÓVEL|COMPARATIVO|HOMOGENEIZA", re.IGNORECASE)
            pag_ini_qr = pag_fim_qr = None
            for i, page in enumerate(_doc):
                if i < 10: continue  # sumário nas primeiras páginas — ignorar
                txt = page.get_text()
                if pag_ini_qr is None and _PAT_FOTOS_QR.search(txt):
                    pag_ini_qr = i
                elif pag_ini_qr is not None and _PAT_STOP.search(txt):
                    pag_fim_qr = i; break
            if pag_ini_qr is None: pag_ini_qr = 10
            if pag_fim_qr is None: pag_fim_qr = len(_doc)
            _log(f"[qr] 📄 Seção de fotos: páginas {pag_ini_qr+1}–{pag_fim_qr}")

            _MARCAS_IGNORAR = ("google","sicar","croqui","mat.","matrícula","-30.","-29.","-28.","-27.")
            xrefs_pdf = set()
            fotos_pdf = []  # [(ordem, xref, bytes, ext, nome)]
            ordem_global = 0

            for page in _doc[pag_ini_qr:pag_fim_qr]:
                imgs = page.get_images(full=True)
                page_w, page_h = page.rect.width, page.rect.height
                blocos = page.get_text("dict")["blocks"]
                textos_pg = []
                for b in blocos:
                    if b["type"] != 0: continue
                    for linha in b["lines"]:
                        t = " ".join(s["text"] for s in linha["spans"]).strip()
                        if t:
                            bx = b["bbox"]
                            textos_pg.append((bx[0], bx[1], bx[2], bx[3], t))

                for img_info in imgs:
                    xref = img_info[0]
                    if xref in xrefs_pdf: continue
                    bi = _doc.extract_image(xref)
                    w, h = bi.get("width", 0), bi.get("height", 0)
                    if w < 150 or h < 150: continue
                    ratio = w/h if h else 0
                    if ratio < 0.3 or ratio > 3.0: continue

                    rects = page.get_image_rects(xref)
                    if not rects: continue
                    r = rects[0]
                    if r.y0 / page_h < 0.08: continue
                    if r.width / page_w < 0.20: continue

                    # Ignorar mapas/QR codes (QR codes têm ratio ~1 e são pequenos)
                    if w < 300 and h < 300 and 0.8 < ratio < 1.2: continue

                    margem = 60
                    viz = " ".join(t for x0,y0,x1,y1,t in textos_pg
                                   if not (x1 < r.x0-margem or x0 > r.x1+margem
                                           or y1 < r.y0-margem or y0 > r.y1+margem)).lower()
                    if any(m in viz for m in _MARCAS_IGNORAR): continue

                    # Título acima da imagem
                    cands = [(y1, t) for x0,y0,x1,y1,t in textos_pg
                             if y1 <= r.y0 + 35 and y1 >= r.y0 - 150
                             and x0 < r.x1 and x1 > r.x0 and _titulo_valido(t)]
                    cands.sort(key=lambda x: x[0], reverse=True)
                    nome_titulo = cands[0][1].strip() if cands else None

                    xrefs_pdf.add(xref)
                    ordem_global += 1
                    fotos_pdf.append((ordem_global, xref, bi["image"], bi["ext"], nome_titulo))

            _doc.close()
            _log(f"[qr] 📷 {len(fotos_pdf)} imagens extraídas do PDF")

            # Montar lista final com nomes normalizados
            for ordem, xref, img_bytes, ext, nome_titulo in fotos_pdf:
                nome_final = _normalizar_nome_foto(nome_titulo) if nome_titulo else "Foto"
                fotos.append({"bytes": img_bytes, "ext": ext, "nome": nome_final})
                _log(f"[qr] ✔ [{ordem}/{len(fotos_pdf)}] {nome_final} ({len(img_bytes)//1024}KB)")

            if fotos:
                return fotos

        except Exception as ex_pdf:
            _log(f"[qr] ⚠️ Erro extração PDF: {ex_pdf}")
            import traceback; traceback.print_exc()

        # Fallback: se extração do PDF falhou, avisa
        _log(f"[qr] ⚠️ Extração direta do PDF não retornou fotos")
        return fotos

        def _baixar_imagem(url_img, debug=False):
            pass  # não usado mais, mantido para compatibilidade

        # Extrair UUID da vistoria a partir das URLs dos QR codes
        _PAT_UUID = re.compile(r"/arquivo/([0-9a-f\-]{36})/", re.IGNORECASE)
        uuid_vistoria = None
        for url_qr, _ in resultados:
            m = _PAT_UUID.search(url_qr)
            if m:
                uuid_vistoria = m.group(1)
                break

        # Montar mapa NOMEARQUIVO → nome do cômodo a partir dos resultados dos QR codes
        mapa_nome_qr = {}
        for url_qr, nome_qr in resultados:
            arq = url_qr.rsplit("/", 1)[-1]
            mapa_nome_qr[arq] = nome_qr

        # Buscar lista completa de imagens da vistoria via API
        lista_imagens_api = []
        if uuid_vistoria:
            try:
                url_vis = f"https://api.infoel.info/multia/buscardadosvistoriaimovel/{uuid_vistoria}"
                r_vis = requests.get(url_vis, headers=hdrs_base, timeout=30)
                if r_vis.status_code == 200:
                    j_vis = r_vis.json()
                    lista_imagens_api = (j_vis.get("dados") or {}).get("imagens") or []
                    _log(f"[qr] 📋 API retornou {len(lista_imagens_api)} imagens para UUID {uuid_vistoria[:8]}...")
                else:
                    _log(f"[qr] ⚠️ API vistoria: status {r_vis.status_code}")
            except Exception as ex_vis:
                _log(f"[qr] ⚠️ Erro ao buscar lista da API: {ex_vis}")

        # Se temos a lista da API, usar ela (mais completa que os QR codes)
        # Caso contrário, usar os resultados dos QR codes
        if lista_imagens_api:
            _log(f"[qr] 🔍 Usando lista da API ({len(lista_imagens_api)} imgs) para download")
            base_url_dl = f"https://{sistema}.infoel.info/arquivo/{uuid_vistoria}"
            for idx, img in enumerate(lista_imagens_api):
                nome_arq = img.get("NOMEARQUIVO") or ""
                if not nome_arq: continue
                descricao = img.get("DESCRICAO") or mapa_nome_qr.get(nome_arq) or "Foto"
                nome_foto = _normalizar_nome_foto(descricao)
                url_dl = f"{base_url_dl}/{nome_arq}"
                try:
                    conteudo = _baixar_imagem(url_dl, debug=(idx < 3))
                    if conteudo:
                        ext = nome_arq.rsplit(".", 1)[-1].lower() if "." in nome_arq else "jpeg"
                        fotos.append({"bytes": conteudo, "ext": ext, "nome": nome_foto})
                        _log(f"[qr] ✔ [{idx+1}/{len(lista_imagens_api)}] {nome_foto} — {nome_arq} ({len(conteudo)//1024}KB)")
                    else:
                        _log(f"[qr] ✗ [{idx+1}] {nome_arq} — sem conteúdo")
                except Exception as ex_dl:
                    _log(f"[qr] ✗ Erro {nome_arq}: {ex_dl}")
        else:
            # Fallback: tentar baixar via URLs dos QR codes
            _log(f"[qr] ⚠️ Lista da API vazia — tentando QR codes diretamente")
            base_url_dl = f"https://{sistema}.infoel.info/arquivo/{uuid_vistoria}" if uuid_vistoria else None
            for idx, (url, nome) in enumerate(resultados):
                try:
                    conteudo = _baixar_imagem(url)
                    if not conteudo and base_url_dl:
                        nome_arq = url.rsplit("/", 1)[-1]
                        conteudo = _baixar_imagem(f"{base_url_dl}/{nome_arq}")
                    if conteudo:
                        ext = url.rsplit(".", 1)[-1].lower() if "." in url.rsplit("/", 1)[-1] else "jpeg"
                        fotos.append({"bytes": conteudo, "ext": ext, "nome": nome})
                        _log(f"[qr] ✔ [{idx+1}/{len(resultados)}] {nome} — {url.rsplit('/',1)[-1]} ({len(conteudo)//1024}KB)")
                    else:
                        _log(f"[qr] ✗ [{idx+1}] {url.rsplit('/',1)[-1]} — sem conteúdo")
                except Exception as ex_dl:
                    _log(f"[qr] ✗ Erro ao baixar {url}: {ex_dl}")

    except Exception as ex:
        _log(f"[qr] ❌ Erro geral: {ex}")
        import traceback; traceback.print_exc()

    return fotos
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        _log(f"[qr] ❌ pypdfium2 não instalado: {e} — rode: pip install pypdfium2")
        return fotos
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        detector = cv2.QRCodeDetector()
        urls_set = set()
        urls_ordem = []
        _STOP_WORDS = ["MEMORIAL DE CÁLCULO","VALOR DO IMÓVEL","INFORMAÇÕES DO AVALIADOR",
                       "INFORMAÇÕES DA EMPRESA","COMPARATIVOS","HOMOGENEIZAÇÃO"]
        n_pags = len(pdf)
        _log(f"[qr] Escaneando {n_pags} páginas do PDF...")
        for i in range(10, n_pags):
            try:
                page_obj = pdf[i]
                textpage = page_obj.get_textpage()
                txt_page = textpage.get_text_range().upper()
            except Exception:
                txt_page = ""
            if any(x in txt_page for x in _STOP_WORDS):
                _log(f"[qr] Página {i+1}: seção técnica — parando")
                break
            try:
                bitmap = page_obj.render(scale=2.5)
                pil_img = bitmap.to_pil()
                img_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
                retval, decoded_info, _, _ = detector.detectAndDecodeMulti(img_bgr)
                novos = 0
                if retval and decoded_info:
                    for info in decoded_info:
                        if info and info.startswith("http") and info not in urls_set:
                            urls_set.add(info); urls_ordem.append(info); novos += 1
                if novos:
                    _log(f"[qr] Página {i+1}: {novos} QR(s) novos (total: {len(urls_ordem)})")
            except Exception as ex_pag:
                _log(f"[qr] ⚠️ Erro na página {i+1}: {ex_pag}")
        _log(f"[qr] {len(urls_ordem)} URL(s) encontradas")
        if not urls_ordem:
            return fotos
        # Montar headers para download — tenta primeiro só com jwt/authorization
        hdrs_base = {"User-Agent": "Mozilla/5.0", "Accept": "image/jpeg,image/*,*/*"}
        if jwt_headers:
            for k in ("authorization", "jwt", "Authorization", "JWT"):
                if k in jwt_headers:
                    hdrs_base[k] = jwt_headers[k]

        # Detectar o sistema a partir do Origin nos headers
        origin = ""
        if jwt_headers:
            origin = jwt_headers.get("Origin") or jwt_headers.get("origin") or ""
        if origin:
            hdrs_base["Origin"] = origin
            hdrs_base["Referer"] = origin + "/"

        for idx, url in enumerate(sorted(urls_ordem)):
            try:
                r = requests.get(url, headers=hdrs_base, timeout=30)
                r.raise_for_status()
                if len(r.content) == 0:
                    _log(f"[qr] ⚠️ [{idx+1}] Resposta vazia (status {r.status_code}) — tentando sem auth...")
                    r2 = requests.get(url, timeout=30)
                    if len(r2.content) > 0:
                        r = r2
                    else:
                        _log(f"[qr] ✗ [{idx+1}] Sem conteúdo mesmo sem auth. URL: {url}")
                        continue
                ext = url.rsplit(".",1)[-1].lower() if "." in url.rsplit("/",1)[-1] else "jpeg"
                nome_arq = url.rsplit("/",1)[-1]
                m = re.search(r"_(\d+)\.", nome_arq)
                num = int(m.group(1)) if m else idx+1
                nome = f"Foto {num:03d}"
                fotos.append({"bytes":r.content,"ext":ext,"nome":nome})
                _log(f"[qr] ✔ [{idx+1}/{len(urls_ordem)}] {nome_arq} ({len(r.content)//1024}KB)")
            except Exception as ex_dl:
                _log(f"[qr] ✗ Erro ao baixar {url}: {ex_dl}")
    except Exception as ex:
        _log(f"[qr] ❌ Erro geral: {ex}")
        import traceback; traceback.print_exc()
    return fotos


# ──────────────────────────────────────────────────────────────
# SESSÃO AUTENTICADA (necessário para download de /arquivo/...)
# ──────────────────────────────────────────────────────────────
def obter_sessao_autenticada(sistema="multia", log_fn=None):
    """
    Faz login no frontend do infoel e retorna a requests.Session autenticada.
    O servidor PHP autentica /arquivo/ por cookie SESSAO, não por JWT.
    A session mantém os cookies automaticamente entre requisições.
    Tenta múltiplos endpoints de login até encontrar o correto.
    """
    def _log(msg):
        _print(msg)
        if log_fn: log_fn(msg)
    try:
        import base64 as _b64
        auth = AUTH_BASIC_MAIS if sistema == "multiamais" else AUTH_BASIC
        b64_parte = auth.replace("Basic ", "").strip()
        decoded = _b64.b64decode(b64_parte).decode("utf-8")
        usuario, senha = decoded.split(":", 1)
        _log(f"[sessao] Autenticando em {sistema}.infoel.info...")
        base_url = f"https://{sistema}.infoel.info"

        sess = requests.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9",
        })

        tentativas = [
            (f"{base_url}/login",          {"usuario": usuario, "senha": senha}),
            (f"{base_url}/login",          {"login":   usuario, "senha": senha}),
            (f"{base_url}/login",          {"email":   usuario, "senha": senha}),
            (f"{base_url}/auth/login",     {"usuario": usuario, "senha": senha}),
            (f"{base_url}/api/login",      {"usuario": usuario, "senha": senha}),
            (f"https://api.infoel.info/multia/login", {"usuario": usuario, "senha": senha}),
        ]

        for endpoint, payload in tentativas:
            try:
                resp = sess.post(endpoint, data=payload, timeout=15, allow_redirects=True)
                cookie = sess.cookies.get("SESSAO")
                _log(f"[sessao] {endpoint} → {resp.status_code} | SESSAO={'✅ '+cookie[:8]+'...' if cookie else '❌ não encontrado'}")
                if cookie:
                    _log(f"[sessao] ✅ Sessão autenticada!")
                    return sess
            except Exception as ex_t:
                _log(f"[sessao] ⚠️ {endpoint} falhou: {ex_t}")

        _log(f"[sessao] ❌ Nenhum endpoint funcionou — tentando sem sessão")
        return sess
    except Exception as ex:
        _log(f"[sessao] ❌ Erro: {ex}")
        return requests.Session()

# ──────────────────────────────────────────────────────────────
# JANELA PARECER
# ──────────────────────────────────────────────────────────────
