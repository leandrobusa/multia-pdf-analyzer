"""
parecer.py — Geração dos textos de parecer do laudo.
gerar_parecer: despacha para o modelo correto (padrão ou privativo).
_gerar_parecer_padrao: modelo padrão (terrenos urbanos e rurais).
_gerar_parecer_privativo: modelo para imóveis com área privativa (apartamentos, casas em condomínio).
"""
import re
from .config import OPCOES, RISCO_PERGUNTAS, OBS_OPCIONAIS
from .utils import INFRA_ITENS
from .utils import _val_parecer_lookup

# PARECER
# ──────────────────────────────────────────────────────────────
def _val_parecer(op_id, val_ui):
    """Converte valor UI (Title Case) para valor de parecer (minúsculo original)."""
    from .config import OPCOES as _OPCOES
    return _val_parecer_lookup(_OPCOES, op_id, val_ui)

def _formatar_obs_juridica(item: dict) -> str:
    """Formata um item de análise jurídica como observação no parecer."""
    id_av   = item.get("id_av", "")
    subtipo = item.get("subtipo", "")
    credora = item.get("credora")

    # Subtipos que normalmente têm credor/beneficiário
    COM_CREDORA = {"alienação fiduciária", "hipoteca", "penhora", "cessão fiduciária",
                   "usufruto", "anticrese", "arresto", "penhor"}

    subtipo_lower = subtipo.lower()
    tem_credora = credora and any(s in subtipo_lower for s in COM_CREDORA)

    # Usar subtipo como descrição principal (sem valor/detalhes)
    subtipo_txt = subtipo[0].lower() + subtipo[1:] if subtipo else "[PREENCHER]"

    # "em favor de" para pessoas físicas, "em favor da instituição" para pessoas jurídicas
    if tem_credora:
        # Heurística: pessoa jurídica tem palavras como Banco, Cooperativa, Caixa, Sicoob etc.
        _pj_keywords = ("banco", "cooperativa", "caixa", "sicoob", "sicredi", "bradesco",
                        "itaú", "santander", "financeira", "crédito", "s.a.", "ltda", "eireli")
        is_pj = any(k in credora.lower() for k in _pj_keywords)
        prefixo = "em favor da instituição" if is_pj else "em favor de"
        return f"Consta, na {id_av}, {subtipo_txt} vigente, {prefixo} {credora}."
    else:
        return f"Consta, na {id_av}, {subtipo_txt}."


def gerar_parecer(dados_pdf, e):
    modelo = e.get("modelo_parecer","padrao")
    if modelo == "privativo":
        return _gerar_parecer_privativo(dados_pdf, e)
    return _gerar_parecer_padrao(dados_pdf, e)


def _gerar_parecer_privativo(dados_pdf, e):
    d = dados_pdf or {}
    def _s(key, fallback="[PREENCHER]"):
        v = d.get(key)
        if not v or str(v).strip() in ("","null","None","[]"): return fallback
        return str(v).strip()
    _tipo_priv=d.get("tipo_imovel","URBANO").upper()
    end=_s("endereco")
    num = "S/N" if _tipo_priv == "RURAL" else _s("numero","S/N")
    bairro = "Interior" if _tipo_priv == "RURAL" else _s("bairro","")
    cidade=_s("cidade"); uf=_s("uf")
    bairro_s=(", "+bairro) if bairro else ""
    _rod=d.get("rodovias_acesso")
    if isinstance(_rod,list): rod=", ".join(_rod) if _rod else "[PREENCHER]"
    else: rod=str(_rod).strip() if _rod and str(_rod).strip() not in ("","null","None") else "[PREENCHER]"

    ap_num    = e.get("ap_num","[UNIDADE]") or "[UNIDADE]"
    ap_edificio = e.get("ap_edificio","[EDIFÍCIO]") or "[EDIFÍCIO]"
    ap_vagas  = [v for v in (e.get("ap_vagas_nums") or []) if v]
    if len(ap_vagas)==0:
        vagas_txt=""
    elif len(ap_vagas)==1:
        vagas_txt=f", juntamente com a vaga de garagem nº {ap_vagas[0]}"
    else:
        nums_fmt=" e nº ".join([f"nº {v}" for v in ap_vagas])
        vagas_txt=f", juntamente com as vagas de garagem {nums_fmt}"

    # Gênero automático baseado na primeira palavra da unidade
    _FEMININO = {"casa","sala","loja","unidade","cobertura","vaga"}
    _primeira_palavra = ap_num.lower().split()[0] if ap_num else ""
    if _primeira_palavra in _FEMININO:
        _avalia  = "Avalia-se a"
        _localiz = "localizada"
    else:
        _avalia  = "Avalia-se o"
        _localiz = "localizado"

    # Parágrafo 1
    p1=(f"{_avalia} {ap_num}{vagas_txt}, "
        f"{_localiz} no {ap_edificio}, "
        f"situado na {end}, {num if num == 'S/N' else f'Nº {num}'}{bairro_s}, em {cidade}/{uf}.")

    # Parágrafo 2 — empreendimento + lazer
    emp   = e.get("ap_empreendimento","").strip()
    const = e.get("ap_construtora","").strip()
    ano   = e.get("ap_ano","").strip()
    dist_mar = e.get("ap_dist_mar","").strip()
    lazer_sels = e.get("ap_lazer",[])
    lazer_livre= e.get("ap_lazer_livre","").strip()

    p2=""
    if emp or const or ano or dist_mar or lazer_sels or lazer_livre:
        _nome = emp or "[EMPREENDIMENTO]"
        _const = f", construído pela {const}" if const else ""
        _ano   = f" e entregue em {ano}" if ano else ""
        _mar   = f", encontra-se próximo ao mar (aproximadamente {dist_mar})" if dist_mar else ""
        if lazer_sels or lazer_livre:
            todos_lazer = list(lazer_sels)
            if lazer_livre: todos_lazer.append(lazer_livre)
            _lazer_str = ", ".join(todos_lazer)
            _lazer_txt = f" e dispõe de completa infraestrutura de lazer, mobiliada e decorada, incluindo {_lazer_str}."
        else:
            _lazer_txt = "."
        p2=(f"O {_nome}{_const}{_ano}{_mar}{_lazer_txt}")

    # Parágrafo 3 — infraestrutura (igual ao padrão)
    _infra_sels = e.get("infra", INFRA_ITENS)
    if _infra_sels:
        p3 = "A infraestrutura da região conta com " + ", ".join(_infra_sels) + " entre outros serviços públicos e comerciais da região."
    else:
        p3 = "A infraestrutura da região conta com serviços públicos e comerciais da região."

    # Parágrafo 4 — distâncias
    d_rod_ap = e.get("dist_rodovia_ap","").strip()
    d_ctr_ap = e.get("dist_centro_ap","").strip()
    d_rod_txt = f"aproximadamente {d_rod_ap}" if d_rod_ap else "[PREENCHER]"
    d_ctr_txt = f"aproximadamente {d_ctr_ap}" if d_ctr_ap else "[PREENCHER]"
    p4=(f"Situado aproximadamente {d_rod_txt} do acesso à Rodovia {rod} "
        f"e {d_ctr_txt} até o centro comercial, administrativo e de serviços da cidade.")

    # Risco Socioambiental
    risco_bloco = ""
    coop = e.get("coop", "outra")
    if coop in ("maxicredito", "biomas") and e.get("risco"):
        risco_bloco = "\n\nRISCO SOCIOAMBIENTAL\n"
        for i, q in enumerate(RISCO_PERGUNTAS):
            risco_bloco += f"\n{q} {e['risco'][i] if i < len(e['risco']) else 'Não'}"
        risco_bloco += f"\n\nObservações e/ou justificativas: {e.get('risco_obs','Não aplicável.')}"

    # Observações
    obs_num = 2
    obs_texto = ("Obs.1: Para efeitos da avaliação, o imóvel foi considerado livre de penhoras, "
                 "arrestos, hipotecas, contaminação do solo ou ônus de qualquer natureza.")
    for idx in sorted(e.get("obs_extras",[])):
        obs_texto += f"\nObs.{obs_num}: {OBS_OPCIONAIS[idx]}"; obs_num += 1
    obs_adicional = e.get("obs_adicional","").strip()
    if obs_adicional:
        obs_texto += f"\nObs.{obs_num}: {obs_adicional}"; obs_num += 1
    for obs_av in e.get("obs_av_juridica", []):
        obs_texto += f"\nObs.{obs_num}: {obs_av}"; obs_num += 1
    for item in e.get("analise_juridica_selecionada", []):
        desc    = item.get("descricao","")
        subtipo = item.get("subtipo","")
        credora = item.get("credora")
        txt = f"{subtipo}: {desc}"
        if credora: txt += f" (Credora: {credora})"
        obs_texto += f"\nObs.{obs_num}: {txt}"; obs_num += 1

    partes=[p1]
    if p2: partes.append(p2)
    partes+=[p3,p4]
    return "\n\n".join(partes) + risco_bloco + "\n\n" + obs_texto


def _gerar_parecer_padrao(dados_pdf, e):
    d=dados_pdf or {}
    def _s(key, fallback="[PREENCHER]"):
        v = d.get(key)
        if not v or str(v).strip() in ("","null","None","[]"): return fallback
        return str(v).strip()
    tipo=d.get("tipo_imovel","URBANO").upper()
    area=e.get("area_total_texto") or _s("area_total")
    end=_s("endereco")
    num = "S/N" if tipo == "RURAL" else _s("numero","S/N")
    bairro = "Interior" if tipo == "RURAL" else _s("bairro","")
    comp=_s("complemento","")
    cidade=_s("cidade"); uf=_s("uf")
    test=_s("testada","-")
    prof_raw=d.get("profundidade")
    prof_val=_s("profundidade","-") if prof_raw else "-"
    _rod=d.get("rodovias_acesso")
    if isinstance(_rod,list): rod=", ".join(_rod) if _rod else "[PREENCHER]"
    else: rod=str(_rod).strip() if _rod and str(_rod).strip() not in ("","null","None") else "[PREENCHER]"
    comp_s=(comp+", ") if comp else ""; bairro_s=(", "+bairro) if bairro else ""
    d_rod=("aproximadamente "+e["dist_rodovia"]) if e.get("dist_rodovia") else "[PREENCHER]"
    d_ctr=("aproximadamente "+e["dist_centro"])  if e.get("dist_centro")  else "[PREENCHER]"

    # Converter valores UI → parecer
    posicao_ui = e.get("posicao","")
    posicao = _val_parecer("posicao", posicao_ui)

    # Relevo — novo sistema de 2 níveis
    relevo = e.get("relevo_texto","")
    if not relevo:
        relevo = _val_parecer("relevo", e.get("relevo","[PREENCHER]"))

    formato = _val_parecer("formato", e.get("formato","[PREENCHER]"))
    solo = _val_parecer("solo", e.get("solo","[PREENCHER]"))
    cerc_ui = e.get("cercamento","")
    cerc = _val_parecer("cercamento", cerc_ui)
    calc_ui = e.get("calcada","")
    calc = _val_parecer("calcada", calc_ui)
    mfio_ui = e.get("meio_fio","")
    mfio = _val_parecer("meio_fio", mfio_ui)
    via_ui = e.get("tipo_via","Rua")
    via = _val_parecer("tipo_via", via_ui)
    pav = _val_parecer("pavimentacao", e.get("pavimentacao","[PREENCHER]"))
    cons = _val_parecer("conservacao", e.get("conservacao","[PREENCHER]"))
    tipo_rua = _val_parecer("tipo_rua", e.get("tipo_rua","[PREENCHER]"))
    padrao = _val_parecer("padrao", e.get("padrao","[PREENCHER]"))

    # Extras e vegetação
    ext_raw = e.get("extras","")
    ext_parts = [_val_parecer("extras", x.strip()) for x in ext_raw.split(", ") if x.strip()] if ext_raw else []
    ext = (", " + ", ".join(ext_parts)) if ext_parts else ""

    veg_partes=[relevo]
    vex=e.get("veg_extra","")
    if vex:
        for v in vex.split(", "):
            v=v.strip()
            if v: veg_partes.append(_val_parecer("veg_extra", v))

    # Cercamento e calçada — agora aparecem explicitamente quando ausentes
    cerc_txt = ""
    if cerc_ui:
        if "sem cercamento" in cerc.lower():
            cerc_txt = "Sem cercamento"
        else:
            cerc_txt = cerc[0].upper()+cerc[1:]

    calc_txt = ""
    if calc_ui:
        if "sem calçada" in calc.lower():
            calc_txt = "não possui calçada"
        else:
            calc_txt = calc

    partes_rua=[]
    if calc_txt: partes_rua.append(calc_txt)
    if mfio and "sem meio-fio" not in mfio.lower(): partes_rua.append(mfio)

    # ── Parágrafo 1 ──
    encravado = posicao == "area encravada"
    esquina = posicao == "na esquina de quadra"
    rua_esquina = e.get("rua_esquina","").strip()
    rua_imovel = end

    if encravado:
        posicao_txt = "área de terra encravada, com acesso por outros terrenos, portanto sem acesso ao curso da estrada"
        testada_txt = ""
    elif esquina and rua_esquina:
        posicao_txt = f"posicionado na esquina de quadra"
        testada_txt = f", com testada de {test} para a {rua_imovel} e {prof_val} metros para a {rua_esquina}"
    else:
        posicao_txt = f"posicionado {posicao}"
        testada_txt = f", com testada de {test} e {prof_val} metros de profundidade"

    # Montar localização sem vírgulas duplas
    _loc_partes = [f"na {end}, {num if num == 'S/N' else f'Nº {num}'}"]
    if comp: _loc_partes.append(comp)
    if bairro: _loc_partes.append(bairro)
    _loc_str = ", ".join(_loc_partes)
    p1=(f"Trata-se de um TERRENO {tipo} com área total de {area}"
        f", localizado {_loc_str}"
        f", em {cidade}/{uf}, {posicao_txt}"
        f"{testada_txt}"
        f", terreno {', '.join(veg_partes)}"
        f", de formato {formato} e superfície do solo {solo}")

    if cerc_txt:
        p1 += f". {cerc_txt}"
    else:
        p1 += "."

    if partes_rua: p1+=", "+", ".join(partes_rua)
    if ext: p1+=ext
    if not p1.endswith("."): p1+="."

    # ── Parágrafo de Classificações de Área ──
    p_area = ""

    # ── Parágrafo 2 — Via + Descrição do Acesso ──
    rodovia_nome = e.get("rodovia_nome","").strip()
    margem_rodovia = via == "às margens de uma rodovia"

    # Montar prefixo de acesso
    _via_mapa = {
        "rua": "uma rua",
        "estrada": "uma estrada",
        "às margens de uma rodovia": "as margens de uma rodovia",
        "condomínio": "um condomínio",
        "servidão de passagem": "uma servidão de passagem",
    }
    _via_prefix = _via_mapa.get(via, f"uma {via}")

    # Descrição do acesso
    _acesso_checks = e.get("acesso_checks",[])
    _acesso_livre  = e.get("acesso_livre","").strip()
    _acesso_partes = []
    _check_map = {
        "Passa por ponte de madeira":              "com passagem por ponte de madeira",
        "Passa por ponte de concreto":             "com passagem por ponte de concreto",
        "Acesso compartilhado com outros imóveis": "com acesso compartilhado com outros imóveis",
    }
    for c in _acesso_checks:
        if c in _check_map: _acesso_partes.append(_check_map[c])
    _acesso_suffix=""
    if _acesso_partes and _acesso_livre:
        _acesso_suffix=", "+", ".join(_acesso_partes)+", sendo "+_acesso_livre
    elif _acesso_partes:
        _acesso_suffix=", "+", ".join(_acesso_partes)
    elif _acesso_livre:
        _acesso_suffix=", sendo "+_acesso_livre

    if margem_rodovia:
        p2=""
    else:
        p2=(f"Com acesso por {_via_prefix}{_acesso_suffix}, "
            f"{pav}, em {cons}.")

    # ── Parágrafo 3 (infraestrutura) ──
    _infra_sels = e.get("infra", INFRA_ITENS)
    if _infra_sels:
        p3 = "A infraestrutura da região conta com " + ", ".join(_infra_sels) + " entre outros serviços públicos e comerciais da região."
    else:
        p3 = "A infraestrutura da região conta com serviços públicos e comerciais da região."

    # ── Parágrafo 4 ──
    if margem_rodovia:
        nome_rod = rodovia_nome if rodovia_nome else "[NOME DA RODOVIA]"
        p4=(f"Situado às margens da rodovia {nome_rod}, {pav}, em {cons}"
            f", numa região de padrão construtivo {padrao}"
            f" e {d_ctr} até o centro comercial, administrativo e de serviços da cidade.")
    else:
        p4=(f"Situado numa {_via_prefix} {tipo_rua}, numa região de padrão construtivo {padrao}"
            f", próximo ao acesso da Rodovia {rod} ({d_rod})"
            f" e {d_ctr} até o centro comercial, administrativo e de serviços da cidade.")

    risco_bloco=""
    coop=e.get("coop","outra")
    if coop in ("maxicredito", "biomas") and e.get("risco"):
        risco_bloco="\n\nRISCO SOCIOAMBIENTAL\n"
        for i,q in enumerate(RISCO_PERGUNTAS):
            risco_bloco+=f"\n{q} {e['risco'][i] if i<len(e['risco']) else 'Não'}"
        risco_bloco+=f"\n\nObservações e/ou justificativas: {e.get('risco_obs','Não aplicável.')}"

    obs_num=2
    obs_texto=("Obs.1: Para efeitos da avaliação, o imóvel foi considerado livre de penhoras, "
               "arrestos, hipotecas, contaminação do solo ou ônus de qualquer natureza.")
    for idx in sorted(e.get("obs_extras",[])):
        obs_texto+=f"\nObs.{obs_num}: {OBS_OPCIONAIS[idx]}"; obs_num+=1
    # Uso das terras (rural) — obrigatório
    uso_terras_list = e.get("uso_terras", [])
    if isinstance(uso_terras_list, list):
        uso_terras_str = ", ".join(uso_terras_list) if uso_terras_list else ""
    else:
        uso_terras_str = str(uso_terras_list).strip()
    if tipo == "RURAL" and uso_terras_str:
        obs_texto += f"\nObs.{obs_num}: Uso atual das terras: {uso_terras_str}."; obs_num += 1
    obs_adicional = e.get("obs_adicional","").strip()
    if obs_adicional:
        obs_texto+=f"\nObs.{obs_num}: {obs_adicional}"; obs_num+=1
    for item in e.get("analise_juridica_selecionada", []):
        obs_texto += f"\nObs.{obs_num}: {_formatar_obs_juridica(item)}"; obs_num+=1

    # Montar texto final
    partes = [p1]
    if p_area: partes.append(p_area)
    if p2: partes.append(p2)
    partes += [p3, p4]
    return "\n\n".join(partes) + risco_bloco + "\n\n" + obs_texto

# ──────────────────────────────────────────────────────────────
# API MULTIA
# ──────────────────────────────────────────────────────────────
class MultiaAPI:
    def __init__(self, jwt, sistema="multia"):
        auth = AUTH_BASIC_MAIS if sistema == "multiamais" else AUTH_BASIC
        self.headers={"authorization":auth,"jwt":jwt,"Accept":"*/*",
                      "Origin":f"https://{sistema}.infoel.info","Referer":f"https://{sistema}.infoel.info/"}
    def _url(self,p): return API_BASE+p
    def buscar_avaliacao(self,busca):
        r=requests.get(self._url("/multia/avaliacoes"),headers=self.headers,
                       params={"page":0,"pageSize":50,"busca":busca,"sortField":"","sortOrder":"","REGSTATUS":"","DATACRIACAO":""},timeout=30)
        r.raise_for_status(); return r.json().get("dados",{}).get("avaliacoes",[])
    def buscar_vistoria(self,uuid):
        r=requests.get(self._url(f"/multia/buscardadosvistoriaimovel/{uuid}"),headers=self.headers,timeout=30)
        r.raise_for_status(); return r.json().get("dados",{})
    def buscar_dados_avaliacao(self,uuid):
        r=requests.get(self._url(f"/multia/dadosavaliacao/{uuid}"),headers=self.headers,timeout=30)
        r.raise_for_status(); return r.json().get("dados",{})

    def salvar_campo(self,uuid,campo_id,valor):
        r=requests.post(self._url(f"/multia/editaravaliacao/{uuid}"),headers=self.headers,
                        data={f"campoTipoBem_{campo_id}": valor},timeout=30)
        r.raise_for_status(); return r.json()

    def salvar_campo_direto(self,uuid,chave,valor):
        """Salva um campo direto no endpoint editaravaliacao (ex: COMPLEMENTO, PARECERLAUDO)."""
        r=requests.post(self._url(f"/multia/editaravaliacao/{uuid}"),headers=self.headers,
                        data={chave: valor},timeout=30)
        r.raise_for_status(); return r.json()

    def salvar_parecer(self,uuid,texto,log_fn=None):
        # Primeiro tenta campo tipoBem com "parecer" no nome
        dados=self.buscar_dados_avaliacao(uuid)
        campos=dados.get("camposTipoBem",[])
        campo_parecer=None
        for c in campos:
            nome=(c.get("NOMECAMPO") or c.get("LABEL") or c.get("NOME") or "").lower()
            if "parecer" in nome:
                campo_parecer=c
                if log_fn: log_fn(f"   ✔ Campo parecer via campoTipoBem: '{nome}' (REG={c.get('REG')})")
                break
        if campo_parecer:
            cid=campo_parecer.get("REG") or campo_parecer.get("ID")
            r=requests.post(self._url(f"/multia/editaravaliacao/{uuid}"),headers=self.headers,
                            data={f"campoTipoBem_{cid}": texto},timeout=60)
            if log_fn: log_fn(f"   📡 Resposta: {r.status_code}")
            r.raise_for_status()
        # Sempre envia também via PARECERLAUDO direto (garante compatibilidade)
        if log_fn: log_fn("   📤 Enviando PARECERLAUDO...")
        r=requests.post(self._url(f"/multia/editaravaliacao/{uuid}"),headers=self.headers,
                        data={"PARECERLAUDO": texto},timeout=60)
        if log_fn: log_fn(f"   📡 Resposta PARECERLAUDO: {r.status_code}")
        r.raise_for_status(); return r.json()

    def salvar_campos_capa(self,uuid,dados_pdf,log_fn=None,tipo_laudo="normal"):
        dados=self.buscar_dados_avaliacao(uuid)
        campos=dados.get("camposTipoBem",[])
        dados_atuais={str(d.get("REGCAMPO") or d.get("REG","")): d.get("VALOR","")
                      for d in dados.get("dadosTipoBem",[])}
        if log_fn: log_fn(f"   {len(campos)} campos encontrados na avaliação")

        # ── 1. Campos diretos no payload editaravaliacao ──
        DIRETOS={
            "COMPLEMENTO": "complemento",
            "ENDERECO":    "endereco",
            "NUMERO":      "numero",
            "BAIRRO":      "bairro",
            "CIDADE":      "cidade",
            "UF":          "uf",
        }
        CAMPOS_TITLE_NOME = {"ENDERECO","BAIRRO","CIDADE","COMPLEMENTO"}
        _tipo_imovel_capa = str(dados_pdf.get("tipo_imovel") or "").upper()
        payload_direto={}
        for chave_api,chave_pdf in DIRETOS.items():
            val=dados_pdf.get(chave_pdf)
            # Imóvel rural → bairro sempre "Interior"
            if chave_api == "BAIRRO" and _tipo_imovel_capa == "RURAL":
                val = "Interior"
            else:
                if isinstance(val,list): val=", ".join(str(v) for v in val)
                if val: val=str(val).strip()
                if not val or val in ("null","None"): val="-"
            if chave_api in CAMPOS_TITLE_NOME and val != "-": val=_title_nome(val)
            payload_direto[chave_api]=val
        if payload_direto:
            if log_fn:
                for k,v in payload_direto.items():
                    log_fn(f"   ✔ [direto] {k} → '{v}'")
            r=requests.post(self._url(f"/multia/editaravaliacao/{uuid}"),headers=self.headers,
                            data=payload_direto,timeout=30)
            r.raise_for_status()

        # ── 2. Campos dinâmicos via campoTipoBem_X ──
        # "coordenada" só é ignorado em laudo normal; no antigo pode haver campo de coords
        _ignorar_coords = tipo_laudo == "normal"
        IGNORAR_CONTEM=["cep","matricula","fonte do croqui","fonte croqui",
                        "placa","renavam","chassi","tração","espécie","categoria veicular",
                        "modelo","marca","combustível","emplacamento","série","fabricação",
                        "horas em uso","dimensões","capacidade","peso","comprimento",
                        "município emplacamento","nº de série","n° de série","descri"]
        MAPA_TIPOBEM={
            "cri":              "cri",
            "testada":          "testada",
            "incra":            "incra",
            "inscr":            "incra",
            "imob":             "incra",
            "inscrição":        "incra",
            "inscricao":        "incra",
            "cidades vizinhas": "cidades_vizinhas",
            "cidade vizinha":   "cidades_vizinhas",
            "rodovias de acesso":"rodovias_acesso",
            "rodovia de acesso":"rodovias_acesso",
            "rodovias":         "rodovias_acesso",
            "car":              "car",
            "proprietár":       "proprietarios",
            "proprietar":       "proprietarios",
            "proprietários":    "proprietarios",
            "proprietarios":    "proprietarios",
            "área total":       "area_total",
            "area total":       "area_total",
            "área (m":          "area_total",
            "area (m":          "area_total",
            "averbac":          "averbacoes",
            "coordenada":       "coordenadas_raw",
        }
        _norm_area = normalizar_area  # usa função global

        salvos=0; ignorados=0; nao_mapeados=[]
        for campo in campos:
            nome_campo=(campo.get("NOMECAMPO") or campo.get("LABEL") or campo.get("NOME") or "").lower().strip()
            cid=campo.get("REG") or campo.get("ID")
            if not cid: continue
            if any(ig in nome_campo for ig in IGNORAR_CONTEM):
                ignorados+=1; continue
            if _ignorar_coords and "coordenada" in nome_campo:
                ignorados+=1; continue
            valor=None; chave_matched=None
            for km,kp in MAPA_TIPOBEM.items():
                if km in nome_campo:
                    valor=dados_pdf.get(kp); chave_matched=kp; break
            if valor is None:
                nao_mapeados.append(nome_campo); continue
            if isinstance(valor,list) and not valor: continue
            if isinstance(valor,list): valor=", ".join(str(v) for v in valor)
            valor=str(valor).strip()
            if not valor or valor in ("null","None"): valor="-"
            if chave_matched=="area_total": valor=_norm_area(valor)
            if chave_matched in ("cri","proprietarios"): valor=_title_nome(valor)
            val_atual=dados_atuais.get(str(cid),"")
            if val_atual and str(val_atual).strip() not in ("","-","null"):
                if log_fn: log_fn(f"   📝 '{nome_campo}' → '{valor}'")
            else:
                if log_fn: log_fn(f"   ✔ '{nome_campo}' → '{valor}'")
            self.salvar_campo(uuid,cid,valor); salvos+=1

        if log_fn:
            log_fn(f"   ✅ {salvos} campoTipoBem preenchido(s), {ignorados} ignorado(s)")
            if nao_mapeados:
                log_fn(f"   ℹ️  Não mapeados: {', '.join(set(nao_mapeados))}")
    def salvar_dados_croqui(self,uuid,coords,fonte="Croqui baseado na descrição da matrícula do imóvel."):
        r=requests.post(self._url(f"/multia/salvardadoscroqui/{uuid}"),headers=self.headers,
                        data={"fonteCroqui":fonte,"coordenadas":coords},timeout=30)
        r.raise_for_status(); return r.json()

    def salvar_imagem_croqui(self,uuid,img,filename="croqui.kml"):
        hdrs={k:v for k,v in self.headers.items() if k.lower()!="content-type"}
        r=requests.post(self._url(f"/multia/salvarimagemcroqui/{uuid}"),headers=hdrs,
                        files={"IMAGEM":(filename,img,"application/vnd.google-earth.kml+xml")},timeout=60)
        r.raise_for_status(); return r.json()

    def adicionar_grupo(self,uuid,nome):
        r=requests.post(self._url(f"/multia/adicionargrupoimovel/{uuid}"),headers=self.headers,
                        data={"NOME":nome},timeout=30)
        r.raise_for_status()
        try: return r.json()
        except Exception: return {}

    def editar_grupo(self,uuid,reg,nome=None,construcao="N",averbado="N",area="",unidade="m²",valor_unidade="",obs="",nao_se_aplica=False):
        aplica="N" if nao_se_aplica else ("S" if area and str(area).strip() not in ("","0","0.00") else "N")
        # API espera ponto como decimal no campo AREA
        area_api = str(area).replace(",",".") if area else "0"
        payload={"CONSTRUCAO":construcao,"AVERBADO":averbado,
                 "AREA":("0" if nao_se_aplica else area_api),"APLICAAREA":aplica,
                 "TIPOMEDIDA":unidade,"VALORUNIDADE":valor_unidade,"OBS":obs}
        if nome: payload["NOME"]=nome
        r=requests.post(self._url(f"/multia/salvargrupoimovel/{uuid}/{reg}"),headers=self.headers,
                        data=payload,timeout=30)
        r.raise_for_status(); return r.json()

    def salvar_imagem_grupo(self,uuid,reg,img,filename="foto.jpg"):
        hdrs={k:v for k,v in self.headers.items() if k.lower()!="content-type"}
        r=requests.post(self._url(f"/multia/salvarimagemgrupoimovel/{uuid}/{reg}"),headers=hdrs,
                        files={"arquivo":(filename,img,"image/jpeg")},timeout=60)
        r.raise_for_status(); return r.json()

    def editar_descricao_imagem(self,uuid,reg_grupo,reg_imagem,descricao):
        """Atualiza a descrição (DESCRICAO) de uma imagem de grupo."""
        r=requests.post(self._url(f"/multia/editarimagemgrupoimovel/{uuid}/{reg_grupo}/{reg_imagem}"),
                        headers=self.headers,data={"DESCRICAO":descricao},timeout=30)
        r.raise_for_status(); return r.json()

    def validar_vistoria_nao_aplica(self,uuid,log_fn=None):
        dados=self.buscar_vistoria(uuid)
        if log_fn:
            chaves=list(dados.keys())
            log_fn(f"   🔍 Chaves retornadas pela API: {chaves}")
        grupos=(dados.get("grupos") or dados.get("gruposImoveis") or [])
        if log_fn: log_fn(f"   📦 {len(grupos)} grupo(s) encontrado(s) via vistoria")
        if grupos and log_fn:
            log_fn(f"   🔑 Chaves do primeiro grupo: {list(grupos[0].keys())}")
        if not grupos:
            if log_fn: log_fn("   ⚠️  grupos vazio — tentando buscar via dadosavaliacao...")
            dados2=self.buscar_dados_avaliacao(uuid)
            grupos=dados2.get("gruposImoveis",[])
            if log_fn: log_fn(f"   📦 {len(grupos)} grupo(s) via dadosavaliacao")
        imagens_por_grupo={}
        for img in (dados.get("imagens") or []):
            rg=img.get("REGGRUPO") or img.get("regGrupo")
            ri=img.get("REG") or img.get("reg")
            desc=str(img.get("DESCRICAO") or img.get("descricao") or "")
            if rg and ri:
                imagens_por_grupo.setdefault(str(rg),[]).append((ri, desc))
        if log_fn and imagens_por_grupo:
            log_fn(f"   🖼️  Imagens por grupo: { {k: len(v) for k,v in imagens_por_grupo.items()} }")

        ja_prec=[]
        for g in grupos:
            reg=g.get("REG")
            nome_raw=str(g.get("NOME") or g.get("nome") or "?")
            nome=_title_grupo(nome_raw)
            area=str(g.get("AREA") or g.get("area") or "").strip()
            vunit=str(g.get("VALORUNIDADE") or g.get("valorUnidade") or "").strip()
            obs=str(g.get("OBS") or g.get("obs") or "").strip()
            constr=g.get("CONSTRUCAO") or g.get("construcao") or "N"
            averb=g.get("AVERBADO") or g.get("averbado") or "N"
            aplica=str(g.get("APLICAAREA") or g.get("aplicaArea") or "").strip()
            if log_fn: log_fn(f"  🔎 '{nome_raw}'→'{nome}' — AREA={area!r} OBS={obs!r} APLICAAREA={aplica!r}")
            def _vazio(v):
                if not v or v.lower() in ("null","none",""): return True
                try: return float(v.replace(",",".")) == 0
                except: return False
            area_vazia  = _vazio(area)
            vunit_vazia = _vazio(vunit)
            obs_vazia   = obs.lower() in ("","não se aplica","nao se aplica","null","none")
            tinha = not area_vazia or not vunit_vazia or not obs_vazia
            if tinha:
                ja_prec.append({"nome":nome,"area":area,"valor":vunit,"obs":obs})
                if log_fn:
                    msg=f"  ⚠️  '{nome}' tinha valor preenchido:"
                    if not area_vazia: msg+=f" Área={area} m²"
                    if not vunit_vazia: msg+=f" | Valor={vunit}"
                    if not obs_vazia: msg+=f" | Obs={obs}"
                    log_fn(msg)
                    log_fn(f"     → Sobrescrevendo com 'Não se aplica'...")
            else:
                if log_fn: log_fn(f"  ✔ '{nome}' estava vazio — marcando 'Não se aplica'")
            self.editar_grupo(uuid,reg,nome=nome,construcao=constr,averbado=averb,
                              area="0",valor_unidade="",obs="",nao_se_aplica=True)
            if log_fn: log_fn(f"     ✔ '{nome}' marcado como 'Não se aplica'")
            for reg_img, desc_atual in imagens_por_grupo.get(str(reg),[]):
                try:
                    # Title da descrição atual da imagem, não do nome do grupo
                    desc_title = _normalizar_nome_foto(desc_atual) if desc_atual else _normalizar_nome_foto(nome)
                    if desc_atual == desc_title:
                        if log_fn: log_fn(f"     🖼️  Imagem {reg_img} — já ok, pulando")
                        continue
                    self.editar_descricao_imagem(uuid,reg,reg_img,desc_title)
                    if log_fn: log_fn(f"     🖼️  Imagem {reg_img} — '{desc_atual}' → '{desc_title}'")
                except Exception as ex:
                    if log_fn: log_fn(f"     ⚠️  Imagem {reg_img} — erro: {ex}")
        return ja_prec


