"""
car_lookup.py — Busca automática do CAR (Cadastro Ambiental Rural) a partir
de uma coordenada, usando os arquivos GeoJSON públicos do Infoterras
(https://cdn.infoterras.com.br/data/{UF}/car/{municipio}.geojson).

O arquivo de cada município contém várias camadas por imóvel (área total,
reserva legal, APP etc, diferenciadas pelo campo "tipo"); aqui usamos só as
features com tipo == "Area do Imovel" (o contorno externo do imóvel) para
localizar em qual delas uma coordenada cai.

ATENÇÃO: a conversão do nome do município para o nome do arquivo é uma
suposição (não documentada pelo Infoterras) — pode falhar para nomes com
grafias incomuns. Nesse caso, buscar_car_por_coordenada retorna None e quem
chamar deve avisar o usuário para conferir manualmente.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

import requests

from .coordenadas import gms_para_decimal

_TIPO_AREA_IMOVEL = "Area do Imovel"


def _to_float_br(s: str) -> float:
    """Converte string numérica com vírgula ou ponto decimal para float."""
    return float(s.replace(",", "."))


def parse_coordenada_livre(texto: str) -> Optional[tuple]:
    """Interpreta uma coordenada digitada livremente (graus decimais ou GMS,
    com ou sem letra de hemisfério N/S/E/W/O), retornando (lat, lon) em
    graus decimais. Retorna None se não reconhecer o formato.
    """
    if not texto:
        return None
    t = texto.strip()

    # 1) GMS completo: 28°12'34.5"S 51°39'15.2"W (com ou sem símbolos de grau)
    parte = r'(\d{1,3})\D+(\d{1,2})\D+(\d{1,2}(?:[.,]\d+)?)\D*([NnSsEeWwOo])'
    m = re.search(parte + r'\D+' + parte, t)
    if m:
        hem_lat = m.group(4).upper()
        hem_lon = 'W' if m.group(8).upper() == 'O' else m.group(8).upper()
        lat = gms_para_decimal(float(m.group(1)), float(m.group(2)), float(m.group(3).replace(",", ".")), hem_lat)
        lon = gms_para_decimal(float(m.group(5)), float(m.group(6)), float(m.group(7).replace(",", ".")), hem_lon)
        return lat, lon

    # 2) Graus decimais com letra de hemisfério: 28.12345 S, 51.65432 W
    m = re.search(
        r'(\d{1,3}[.,]\d+)\s*°?\s*([NnSs])\s*[,;]?\s*(\d{1,3}[.,]\d+)\s*°?\s*([EeWwOo])',
        t
    )
    if m:
        lat = _to_float_br(m.group(1))
        lon = _to_float_br(m.group(3))
        if m.group(2).upper() == 'S': lat = -abs(lat)
        if m.group(4).upper() in ('W', 'O'): lon = -abs(lon)
        return lat, lon

    # 3) Graus decimais com ponto e sinal negativo, separados por vírgula/;/espaço
    m = re.search(r'(-?\d{1,3}\.\d+)\s*[,;]?\s*(-?\d{1,3}\.\d+)', t)
    if m:
        return float(m.group(1)), float(m.group(2))

    # 4) Graus decimais com vírgula como decimal (BR), separados por ; ou espaço
    m = re.search(r'(-?\d{1,3},\d+)\s*[;]\s*(-?\d{1,3},\d+)', t) or \
        re.search(r'(-?\d{1,3},\d+)\s+(-?\d{1,3},\d+)', t)
    if m:
        return _to_float_br(m.group(1)), _to_float_br(m.group(2))

    return None


def _slug_municipio(nome: str) -> str:
    """Converte o nome oficial do município para o formato usado nos
    arquivos do Infoterras (ex: 'André da Rocha' -> 'andre_da_rocha')."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", sem_acento).strip("_").lower()
    return slug


def _baixar_geojson_municipio(uf: str, cidade: str, log_fn=None) -> Optional[dict]:
    def _log(msg):
        if log_fn:
            log_fn(msg)

    slug = _slug_municipio(cidade)
    url = f"https://cdn.infoterras.com.br/data/{uf.upper()}/car/{slug}.geojson"
    try:
        resp = requests.get(url, timeout=30, headers={
            "Origin": "https://infoterras.com.br",
            "Referer": "https://infoterras.com.br/",
        })
        if resp.status_code == 404:
            _log(f"[car] ⚠️ Arquivo não encontrado para '{cidade}/{uf}' (tentei: {slug}.geojson). "
                 "O nome do arquivo pode ser diferente do esperado — confira manualmente em infoterras.com.br.")
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as ex:
        _log(f"[car] ⚠️ Erro ao baixar dados do município: {ex}")
        return None


def buscar_car_por_coordenada(uf: str, cidade: str, lat: float, lon: float, log_fn=None) -> Optional[dict]:
    """Busca o imóvel do CAR que contém a coordenada informada.

    Args:
        uf: sigla do estado (ex: 'RS').
        cidade: nome do município (ex: 'André da Rocha').
        lat: latitude em graus decimais.
        lon: longitude em graus decimais.
        log_fn: função opcional de log.

    Returns:
        {"car": id_do_car, "area": area_ha, "status": ..., "cond": ...,
         "pontos": [(lat,lon), ...]} — pontos do contorno externo, prontos
        para gerar KML. None se não encontrado ou se o arquivo do
        município não pôde ser localizado.
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    from shapely.geometry import shape, Point

    geojson = _baixar_geojson_municipio(uf, cidade, log_fn=log_fn)
    if not geojson:
        return None

    features = geojson.get("features", [])
    _log(f"[car] {len(features)} feature(s) no arquivo de {cidade}/{uf}")

    ponto = Point(lon, lat)  # GeoJSON usa (longitude, latitude)

    for feat in features:
        props = feat.get("properties", {})
        if props.get("tipo") != _TIPO_AREA_IMOVEL:
            continue
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            geometria = shape(geom)
        except Exception:
            continue
        if geometria.contains(ponto):
            # Extrai o contorno externo (lat, lon) para uso em KML
            if geometria.geom_type == "MultiPolygon":
                maior = max(geometria.geoms, key=lambda g: g.area)
                anel = maior.exterior
            else:
                anel = geometria.exterior
            pontos = [(y, x) for x, y in anel.coords]  # (lat, lon)
            return {
                "car": props.get("id"),
                "area": props.get("area"),
                "status": props.get("status"),
                "cond": props.get("cond"),
                "pontos": pontos,
            }

    _log(f"[car] ⚠️ Nenhum imóvel encontrado contendo a coordenada {lat}, {lon}")
    return None
