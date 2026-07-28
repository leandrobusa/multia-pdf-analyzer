"""
utils.py — Funções utilitárias de texto e formatação.
Usadas por parecer.py, api.py e pela interface.
"""
import re

def normalizar_area(v):
    """
    Converte área para formato que o sistema espera: sem ponto de milhar,
    vírgula como separador decimal. Ex: '11.324,26 m²' → '11324,26'
    """
    import re as _r
    if not v: return v
    n = _r.sub(r"[^\d.,]", "", str(v)).strip()
    if not n: return v
    # Formato BR com milhar: 11.324,26 → 11324,26
    if _r.match(r"^\d{1,3}(\.\d{3})+,\d+$", n):
        return n.replace(".", "")
    # Formato americano com ponto decimal: 11324.26 ou 11,324.26 → 11324,26
    if _r.match(r"^\d{1,3}(,\d{3})*\.\d+$", n) or _r.match(r"^\d+\.\d{1,2}$", n):
        return n.replace(",", "").replace(".", ",")
    # Já correto (sem ponto, com vírgula): 11324,26
    return n

# ──────────────────────────────────────────────────────────────
MINUSCULAS_PT = {"de","da","do","das","dos","e","a","o","as","os","em","na","no","nas","nos","com","por","para","ao","aos","à","às","um","uma"}

def _title_nome(texto):
    """Title case para nomes próprios e logradouros: preposições/artigos em minúsculo."""
    if not texto: return texto
    palavras = str(texto).split()
    resultado = []
    for i, p in enumerate(palavras):
        if i == 0 or p.lower() not in MINUSCULAS_PT:
            resultado.append(p.capitalize())
        else:
            resultado.append(p.lower())
    return " ".join(resultado)

def _title_grupo(nome):
    """Title case para nome de grupo: insere espaço entre letra e número colados, depois aplica _title_nome."""
    import re
    nome = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", nome)
    nome = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", nome)
    return _title_nome(nome)

CORRECOES_DIGITACAO = {
    "faixada": "fachada",
    "sala de star": "sala de estar",
    "clouset": "closet",
    "cloust": "closet",
    "dispensa": "despensa",
}

def _normalizar_nome_foto(nome):
    """Title case para nomes de fotos + substituições de terminologia.
    Regras:
    - Primeira letra maiúscula
    - BWC sempre em maiúsculas
    - Número colado à palavra ganha espaço (casa1 → Casa 1)
    - Após hífen, primeira letra maiúscula
    - Corrige erros de digitação comuns (ver CORRECOES_DIGITACAO)
    - banheiro → BWC, lavanderia → Área de serviço, casa → Residência, quarto → Dormitório
    """
    if not nome: return "Foto"
    # Corrige erros de digitação comuns antes de qualquer outra substituição
    n = nome
    for errado, certo in CORRECOES_DIGITACAO.items():
        n = re.sub(r'\b' + re.escape(errado) + r'\b', certo, n, flags=re.IGNORECASE)
    # Substituições antes do title case
    n = re.sub(r'\bbanheiro\b', 'BWC', n, flags=re.IGNORECASE)
    n = re.sub(r'\blavanderia\b', 'Área de serviço', n, flags=re.IGNORECASE)
    n = re.sub(r'\bcasa\b', 'Residência', n, flags=re.IGNORECASE)
    n = re.sub(r'\bquarto\b', 'Dormitório', n, flags=re.IGNORECASE)
    # Espaço entre letra e número colados
    n = re.sub(r'([A-Za-zÀ-ÿ])(\d)', r'\1 \2', n)
    n = re.sub(r'(\d)([A-Za-zÀ-ÿ])', r'\1 \2', n)
    # Title case em cada segmento separado por hífen
    # Apenas a primeira palavra de cada segmento é capitalizada
    partes = n.split(' - ')
    partes_final = []
    for parte in partes:
        palavras = parte.split()
        palavras_final = []
        for i, p in enumerate(palavras):
            # BWC sempre maiúsculo
            if p.lower() == 'bwc':
                palavras_final.append('BWC')
            elif i == 0:
                palavras_final.append(p.capitalize())
            else:
                palavras_final.append(p.lower())
        partes_final.append(' '.join(palavras_final))
    n = ' - '.join(partes_final)
    # Garantir BWC maiúsculo em qualquer posição restante
    n = re.sub(r'\bBwc\b', 'BWC', n)
    n = re.sub(r'\bbwc\b', 'BWC', n, flags=re.IGNORECASE)
    return n

INFRA_ITENS = [
    "rede de abastecimento de água e coleta de esgoto",
    "energia elétrica",
    "coleta de lixo e recicláveis",
    "rede de telefonia fixa e móvel",
    "escolas",
    "supermercados",
    "postos de combustíveis",
    "linhas de transporte público",
]

OPCOES_FONTE_CROQUI = [
    "Croqui baseado nas informações do CAR, confirmadas pelo proprietário do imóvel.",
    "Croqui baseado nas informações do CAR. Não foi possível coletar informações com o associado.",
    "Croqui baseado no georreferenciamento presente na descrição da matrícula.",
    "Croqui baseado na descrição do proprietário.",
    "Croqui baseado na descrição do proprietário. O município não possui sistema de georreferenciamento para confirmação.",
    "Croqui baseado no georreferenciamento municipal. Informações confirmadas pelo proprietário.",
    "Croqui baseado na descrição do proprietário. Sistema de georreferenciamento municipal indisponível para confirmação.",
    "Croqui baseado na descrição do acompanhante da vistoria e descrição da matrícula.",
    "Croqui baseado na descrição do acompanhante da vistoria e descrição da matrícula. Informações do Sistema de Georreferenciamento conflitantes.",
    "Croqui baseado na descrição da matrícula do imóvel.",
    "Croqui baseado na descrição do acompanhante da vistoria. Sistema CAR indisponível para confirmação.",
    "Croqui baseado na descrição do acompanhante da vistoria. Sistema CAR com informações conflitantes.",
]

# ──────────────────────────────────────────────────────────────

def _val_parecer_lookup(opcoes_lista, op_id, val_ui):
    """Converte valor UI (Title Case) para valor de parecer (minúsculo)."""
    for op in opcoes_lista:
        if op["id"] == op_id and "opcoes_parecer" in op:
            try:
                idx = op["opcoes"].index(val_ui)
                return op["opcoes_parecer"][idx]
            except (ValueError, IndexError):
                pass
    return val_ui.lower() if val_ui else val_ui



def resource_path(file):
    """Retorna o caminho correto tanto em desenvolvimento quanto no .exe gerado pelo PyInstaller."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, file)


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


