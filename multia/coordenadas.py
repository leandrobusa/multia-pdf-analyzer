"""
coordenadas.py — Conversão e exportação de coordenadas geográficas.

Suporta os formatos UTM, GMS (Graus/Minutos/Segundos) e Decimal.
Exporta polígonos para KML (Google Earth) e SHP (Shapefile).
Calcula polígonos a partir de azimutes e distâncias.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Conversões de coordenadas
# ---------------------------------------------------------------------------

def gms_para_decimal(graus: float, minutos: float, segundos: float, hemisferio: str) -> float:
    """Converte coordenadas GMS para graus decimais.

    Args:
        graus: Componente de graus.
        minutos: Componente de minutos.
        segundos: Componente de segundos.
        hemisferio: "S" ou "W" para negativo, "N" ou "E" para positivo.

    Returns:
        Valor em graus decimais (negativo para S/W).
    """
    decimal = abs(float(graus)) + float(minutos) / 60 + float(segundos) / 3600
    return -decimal if hemisferio in ("S", "W") else decimal


# Mantém compatibilidade com código legado
gms_to_decimal = gms_para_decimal


def utm_para_latlon(easting: float, northing: float, zona: int, hemisferio_sul: bool = True) -> tuple[float, float]:
    """Converte coordenadas UTM para latitude/longitude WGS84.

    Args:
        easting: Coordenada E (metros).
        northing: Coordenada N (metros).
        zona: Número da zona UTM.
        hemisferio_sul: True para hemisfério sul.

    Returns:
        Tupla (latitude, longitude) em graus decimais.
    """
    k0 = 0.9996
    a  = 6_378_137.0
    e  = 0.0818191908426

    x = easting - 500_000.0
    y = northing - 10_000_000.0 if hemisferio_sul else northing
    M = y / k0

    mu = M / (a * (1 - e**2 / 4 - 3 * e**4 / 64 - 5 * e**6 / 256))
    e1 = (1 - math.sqrt(1 - e**2)) / (1 + math.sqrt(1 - e**2))
    fp = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )

    e2 = (e * a / math.sqrt(a**2 * (1 - e**2))) ** 2
    C1 = e2 * math.cos(fp) ** 2
    T1 = math.tan(fp) ** 2
    R1 = a * (1 - e**2) / (1 - (e * math.sin(fp)) ** 2) ** 1.5
    N1 = a / math.sqrt(1 - (e * math.sin(fp)) ** 2)
    D  = x / (N1 * k0)

    lat = fp - (N1 * math.tan(fp) / R1) * (
        D**2 / 2
        - (5 + 3 * T1 + 10 * C1 - 4 * C1**2 - 9 * e2) * D**4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1**2 - 252 * e2 - 3 * C1**2) * D**6 / 720
    )
    lon = (
        (D - (1 + 2 * T1 + C1) * D**3 / 6
         + (5 - 2 * C1 + 28 * T1 - 3 * C1**2 + 8 * e2 + 24 * T1**2) * D**5 / 120)
        / math.cos(fp)
    ) + (zona * 6 - 183) * math.pi / 180

    return math.degrees(lat), math.degrees(lon)


# Alias para compatibilidade
utm_to_latlon = utm_para_latlon


def normalizar_pontos(coord: dict) -> list[tuple[float, float]]:
    """Converte pontos de qualquer formato suportado para (lat, lon) WGS84.

    Args:
        coord: Dicionário com "formato" e "pontos" (e opcionalmente
               "zona_utm" e "hemisferio").

    Returns:
        Lista de tuplas (latitude, longitude).

    Raises:
        ValueError: Se o formato não for reconhecido.
    """
    fmt  = (coord.get("formato") or "").upper()
    pts  = coord.get("pontos", [])

    if fmt == "UTM":
        zona        = int(coord.get("zona_utm") or 22)
        hem_sul     = (coord.get("hemisferio") or "S").upper() == "S"
        return [utm_para_latlon(float(p["x"]), float(p["y"]), zona, hem_sul) for p in pts]

    if fmt == "GMS":
        return [
            (
                gms_para_decimal(p["lat_g"], p["lat_m"], p["lat_s"], p["lat_hem"]),
                gms_para_decimal(p["lon_g"], p["lon_m"], p["lon_s"], p["lon_hem"]),
            )
            for p in pts
        ]

    if fmt == "DECIMAL":
        return [(float(p["y"]), float(p["x"])) for p in pts]

    raise ValueError(f"Formato de coordenadas desconhecido: '{fmt}'")


# ---------------------------------------------------------------------------
# Exportação KML
# ---------------------------------------------------------------------------

def gerar_kml_bytes(pontos_latlon: list[tuple[float, float]], nome: str) -> bytes:
    """Gera um arquivo KML com o polígono definido pelos pontos.

    Args:
        pontos_latlon: Lista de tuplas (lat, lon).
        nome: Nome do polígono no KML.

    Returns:
        Conteúdo binário do arquivo KML.
    """
    import simplekml

    kml = simplekml.Kml()
    pol = kml.newpolygon(name=nome)

    coords = [(lon, lat, 0) for lat, lon in pontos_latlon]
    if coords[0] != coords[-1]:
        coords.append(coords[0])

    pol.outerboundaryis              = coords
    pol.style.linestyle.color        = simplekml.Color.yellow
    pol.style.linestyle.width        = 3
    pol.style.polystyle.color        = simplekml.Color.changealphaint(0, simplekml.Color.white)

    tmp = tempfile.NamedTemporaryFile(suffix=".kml", delete=False)
    kml.save(tmp.name)
    tmp.close()

    with open(tmp.name, "rb") as fh:
        data = fh.read()
    os.unlink(tmp.name)
    return data


def gerar_kml_multiplo_uniao(poligonos: list[dict]) -> bytes:
    """Gera um único KML com um polígono por matrícula (cada um com um
    marcador no centro identificando o número da matrícula), mais um
    polígono extra representando a união geométrica de todas ("Área
    Somada"), com um marcador próprio listando as matrículas envolvidas
    separadas por travessão (ex: "1 - 2 - 3").

    Args:
        poligonos: lista de {"pontos": [(lat,lon),...], "label": "34.319"}.

    Returns:
        Conteúdo binário do arquivo KML.

    Raises:
        ImportError: se a biblioteca 'shapely' não estiver instalada
            (necessária para calcular a união dos polígonos).
    """
    import simplekml
    from shapely.geometry import Polygon as _Polygon
    from shapely.ops import unary_union

    kml = simplekml.Kml()

    def _estilizar(pol):
        pol.style.linestyle.color = simplekml.Color.yellow
        pol.style.linestyle.width = 3
        pol.style.polystyle.color = simplekml.Color.changealphaint(0, simplekml.Color.white)

    shapes_validas = []
    for item in poligonos:
        pontos_latlon = item["pontos"]
        label         = item["label"]

        pol = kml.newpolygon(name=label)
        coords = [(lon, lat, 0) for lat, lon in pontos_latlon]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        pol.outerboundaryis = coords
        _estilizar(pol)

        centro_lat = sum(p[0] for p in pontos_latlon) / len(pontos_latlon)
        centro_lon = sum(p[1] for p in pontos_latlon) / len(pontos_latlon)
        kml.newpoint(name=label, coords=[(centro_lon, centro_lat)])

        try:
            shapes_validas.append(_Polygon([(lon, lat) for lat, lon in pontos_latlon]))
        except Exception:
            pass

    # Polígono da área somada — união geométrica de todas as matrículas
    if len(shapes_validas) > 1:
        try:
            uniao  = unary_union(shapes_validas)
            partes = list(uniao.geoms) if uniao.geom_type == "MultiPolygon" else [uniao]

            for parte in partes:
                pol_soma = kml.newpolygon(name="Área Somada")
                pol_soma.outerboundaryis = [(x, y, 0) for x, y in parte.exterior.coords]
                _estilizar(pol_soma)

            # Pin único no centro da área somada, com as matrículas separadas por travessão
            centro = uniao.centroid
            nomes  = " - ".join(item["label"] for item in poligonos)
            kml.newpoint(name=nomes, coords=[(centro.x, centro.y)])
        except Exception:
            pass  # união geométrica falhou (ex: geometria inválida) — mantém só os polígonos individuais

    tmp = tempfile.NamedTemporaryFile(suffix=".kml", delete=False)
    kml.save(tmp.name)
    tmp.close()

    with open(tmp.name, "rb") as fh:
        data = fh.read()
    os.unlink(tmp.name)
    return data


# ---------------------------------------------------------------------------
# Exportação SHP
# ---------------------------------------------------------------------------

def gerar_shp(pontos_utm: list[dict], zona: int, nome: str, pasta: str) -> str:
    """Gera um Shapefile com o polígono UTM.

    Args:
        pontos_utm: Lista de dicts com "x" (easting) e "y" (northing).
        zona: Número da zona UTM para definir o CRS.
        nome: Nome do arquivo (sem extensão).
        pasta: Diretório de destino.

    Returns:
        Caminho completo do arquivo .shp gerado.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    coords = [(float(p["x"]), float(p["y"])) for p in pontos_utm]
    if coords[0] != coords[-1]:
        coords.append(coords[0])

    gdf  = gpd.GeoDataFrame(index=[0], geometry=[Polygon(coords)], crs=f"EPSG:{31960 + zona}")
    path = os.path.join(pasta, f"{nome}.shp")
    gdf.to_file(path)
    return path


# ---------------------------------------------------------------------------
# Cálculo de polígono a partir de azimutes
# ---------------------------------------------------------------------------

def _parsear_azimute(az_str: str) -> float:
    """Converte string de azimute para graus decimais.

    Aceita os formatos: '175d08m34s', '175°08'34"', '175.142'.

    Args:
        az_str: String representando o azimute.

    Returns:
        Azimute em graus decimais.

    Raises:
        ValueError: Se o formato não for reconhecido.
    """
    az = str(az_str).strip().replace(",", ".")

    # Formato NNNdMMmSSs (padrão solicitado ao Gemini)
    m = re.match(r"(\d+)[dDgG](\d+)[mM]([\d.]+)[sS]?", az)
    if m:
        return float(m.group(1)) + float(m.group(2)) / 60 + float(m.group(3)) / 3600

    # Formato com separadores variados: 175°08'34"
    m = re.match(r"(\d+)[^\d]+(\d+)[^\d]+([\d.]+)", az)
    if m:
        return float(m.group(1)) + float(m.group(2)) / 60 + float(m.group(3)) / 3600

    # Graus decimais puros
    m = re.match(r"([\d.]+)", az)
    if m:
        return float(m.group(1))

    raise ValueError(f"Formato de azimute não reconhecido: {az_str!r}")


def calcular_poligono_azimutes(
    ponto_inicial: dict,
    trechos: list[dict],
) -> list[dict]:
    """Calcula todos os vértices UTM a partir do ponto inicial e sequência de azimutes.

    Args:
        ponto_inicial: Dict com "x" (easting) e "y" (northing) do primeiro vértice.
        trechos: Lista de dicts com "azimute" e "distancia_m".

    Returns:
        Lista de dicts {"x": easting, "y": northing} para cada vértice.
    """
    x = float(ponto_inicial["x"])
    y = float(ponto_inicial["y"])
    pontos = [{"x": x, "y": y}]

    for trecho in trechos:
        az_graus  = _parsear_azimute(str(trecho.get("azimute", "0")))
        distancia = float(str(trecho.get("distancia_m", 0)).replace(",", "."))
        az_rad    = math.radians(az_graus)

        x += distancia * math.sin(az_rad)
        y += distancia * math.cos(az_rad)
        pontos.append({"x": round(x, 3), "y": round(y, 3)})

    return pontos


def montar_coordenadas_de_azimutes(dados_pdf: dict) -> Optional[dict]:
    """Calcula o polígono UTM completo a partir dos azimutes presentes no PDF.

    Usado como complemento quando a análise principal retorna poucos pontos UTM.

    Args:
        dados_pdf: Dicionário retornado pelo Gemini com os dados do PDF.

    Returns:
        Dicionário no formato padrão de coordenadas UTM, ou None se não aplicável.
    """
    azimutes = dados_pdf.get("azimutes")
    if not azimutes:
        return None

    coord = dados_pdf.get("coordenadas")
    if not coord or (coord.get("formato") or "").upper() != "UTM":
        return None

    pontos = coord.get("pontos", [])
    if not pontos:
        return None

    try:
        pontos_calculados = calcular_poligono_azimutes(pontos[0], azimutes)
        return {
            "formato":    "UTM",
            "zona_utm":   coord.get("zona_utm", 22),
            "hemisferio": coord.get("hemisferio", "S"),
            "pontos":     pontos_calculados,
            "origem":     "azimutes",
        }
    except Exception as exc:
        print(f"[coordenadas] Erro ao calcular polígono por azimutes: {exc}")
        return None


# ---------------------------------------------------------------------------
# Croqui a partir de confrontações (frente/direita/fundos/esquerda) + ângulos
# ---------------------------------------------------------------------------

_LADOS_CONFRONTACAO   = ("frente", "direita", "fundos", "esquerda")
_VERTICES_ANGULO      = ("frente_direita", "direita_fundos", "fundos_esquerda", "esquerda_frente")


def _medida_para_metros(valor: Any) -> Optional[float]:
    """Extrai o valor numérico (em metros) de uma string tipo '5,05 metros'.

    Args:
        valor: String ou número com a medida.

    Returns:
        Valor em metros, ou None se não for possível interpretar.
    """
    if valor is None:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(valor))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def calcular_poligono_confrontacoes(
    confrontacoes: Optional[dict],
    angulos_internos: Optional[dict],
) -> Optional[tuple[list[tuple[float, float]], bool]]:
    """Calcula os vértices reais do polígono (plano local, sem georreferência)
    a partir das medidas de frente/direita/fundos/esquerda e dos ângulos
    internos de cada vértice.

    Quando um ângulo não é informado no documento, assume-se 90° (canto reto)
    nesse vértice — é o caso mais comum para lote urbano. O retorno indica se
    algum ângulo foi assumido, para que o desenho final sinalize a aproximação.

    Args:
        confrontacoes: Dict {lado: {medida, descricao}} para os 4 lados.
        angulos_internos: Dict com os ângulos internos informados no documento
            (formato 'XXXdYYmZZs'), com apenas as chaves conhecidas — as que
            faltarem são assumidas como 90°. Pode ser None.

    Returns:
        Tupla (vertices, aproximado): vertices é a lista com os 4 vértices
        (x, y) do polígono, na ordem frente → direita → fundos → esquerda;
        aproximado é True se algum ângulo foi assumido em vez de vir do
        documento. Retorna None se faltar alguma medida de lado ou se o
        polígono resultante não fechar dentro de uma tolerância aceitável.
    """
    if not confrontacoes:
        return None

    try:
        medidas = []
        for lado in _LADOS_CONFRONTACAO:
            metros = _medida_para_metros((confrontacoes.get(lado) or {}).get("medida"))
            if not metros or metros <= 0:
                return None
            medidas.append(metros)

        angulos = []
        aproximado = False
        for chave in _VERTICES_ANGULO:
            valor = (angulos_internos or {}).get(chave)
            if valor:
                angulos.append(_parsear_azimute(str(valor)))
            else:
                angulos.append(90.0)
                aproximado = True
    except (ValueError, AttributeError):
        return None

    x, y = 0.0, 0.0
    heading = 0.0
    pontos = [(x, y)]
    for medida, angulo_interno in zip(medidas, angulos):
        rad = math.radians(heading)
        x += medida * math.cos(rad)
        y += medida * math.sin(rad)
        pontos.append((round(x, 3), round(y, 3)))
        heading += 180.0 - angulo_interno

    fechamento = math.hypot(pontos[-1][0] - pontos[0][0], pontos[-1][1] - pontos[0][1])
    if fechamento > max(medidas) * 0.10:
        return None  # medidas/ângulos não fecham um polígono minimamente coerente

    return pontos[:-1], aproximado


def _escapar_svg(texto: Any) -> str:
    """Escapa caracteres especiais de XML/SVG em texto livre."""
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def gerar_svg_confrontacoes(
    vertices: list[tuple[float, float]],
    confrontacoes: dict,
    distancia_esquina: Optional[dict] = None,
    numero_matricula: str = "",
    aproximado: bool = False,
) -> str:
    """Gera o SVG do croqui a partir do polígono real calculado.

    Args:
        vertices: 4 vértices (x, y) na ordem frente, direita, fundos, esquerda.
        confrontacoes: Dict {lado: {medida, descricao}}.
        distancia_esquina: Dict {distancia_m, referencia} ou None.
        numero_matricula: Número da matrícula, usado no título.
        aproximado: True se algum ângulo interno foi assumido como reto (90°)
            por não estar descrito no documento — exibe um aviso no desenho.

    Returns:
        String SVG completa, pronta para salvar em arquivo.
    """
    if len(vertices) != 4:
        raise ValueError("São esperados exatamente 4 vértices.")

    xs = [p[0] for p in vertices]
    ys = [p[1] for p in vertices]
    largura_real = max(max(xs) - min(xs), 0.01)
    altura_real  = max(max(ys) - min(ys), 0.01)

    area, padding, topo = 480, 70, 60
    escala = min(area / largura_real, area / altura_real)

    def _transforma(p):
        return (
            (p[0] - min(xs)) * escala + padding,
            (p[1] - min(ys)) * escala + padding,
        )

    pts = [_transforma(p) for p in vertices]
    cx = sum(p[0] for p in pts) / 4
    cy = sum(p[1] for p in pts) / 4
    largura_total = area + padding * 2
    altura_total  = area + padding * 2 + topo + 30
    pontos_str = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)

    linhas = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura_total:.0f} {altura_total:.0f}" '
        f'font-family="Arial, sans-serif">',
    ]
    if numero_matricula:
        linhas.append(
            f'<text x="{largura_total/2:.0f}" y="28" font-size="16" font-weight="bold" '
            f'text-anchor="middle" fill="#1a252f">CROQUI — MATRÍCULA Nº '
            f'{_escapar_svg(numero_matricula)}</text>'
        )
    if aproximado:
        linhas.append(
            f'<text x="{largura_total/2:.0f}" y="46" font-size="10" font-style="italic" '
            f'text-anchor="middle" fill="#a15c00">⚠ Ângulo(s) não informado(s) no documento — '
            f'assumido(s) 90° (canto reto)</text>'
        )
    linhas.append(f'<g transform="translate(0,{topo})">')
    linhas.append(f'<polygon points="{pontos_str}" fill="#c8e6c9" stroke="#2e7d32" stroke-width="2.5"/>')

    for i, lado in enumerate(_LADOS_CONFRONTACAO):
        p1, p2 = pts[i], pts[(i + 1) % 4]
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        dx, dy = mx - cx, my - cy
        dist = math.hypot(dx, dy) or 1
        lx, ly = mx + dx / dist * 24, my + dy / dist * 24

        info      = confrontacoes.get(lado) or {}
        medida    = info.get("medida") or ""
        descricao = info.get("descricao") or ""
        rotulo = f"{lado.upper()}: {medida}" + (f" — {descricao}" if descricao else "")
        linhas.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="11" font-weight="bold" '
            f'fill="#1565c0" text-anchor="middle">{_escapar_svg(rotulo)}</text>'
        )

    linhas.append("</g>")

    if distancia_esquina and distancia_esquina.get("distancia_m"):
        texto = f"A {distancia_esquina['distancia_m']} m da {distancia_esquina.get('referencia') or 'esquina'}"
        linhas.append(
            f'<text x="{largura_total/2:.0f}" y="{altura_total - 10:.0f}" font-size="11" '
            f'fill="#555" text-anchor="middle">{_escapar_svg(texto)}</text>'
        )

    linhas.append("</svg>")
    return "\n".join(linhas)
