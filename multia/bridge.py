"""
bridge.py — API Python exposta ao frontend via PyWebView.

Todos os métodos públicos desta classe ficam disponíveis em
window.pywebview.api.* no contexto JavaScript.
"""
from __future__ import annotations

import threading
import os
import json
import re
from tkinter import filedialog, messagebox
import tkinter as tk

from .config import (
    OPCOES, OBS_OPCIONAIS, RISCO_PERGUNTAS,
    JWT_PADRAO, JWT_PADRAO_MAIS,
    AUTH_BASIC, AUTH_BASIC_MAIS, _ENV_CARREGADO
)
from .utils import INFRA_ITENS, OPCOES_FONTE_CROQUI
from .utils import normalizar_area, _title_grupo
from .coordenadas import (
    normalizar_pontos, gerar_kml_bytes, gerar_shp, montar_coordenadas_de_azimutes,
    gerar_kml_multiplo_uniao, calcular_poligono_confrontacoes, gerar_svg_confrontacoes,
)
from .gemini import chamar_gemini, chamar_gemini_azimutes, chamar_gemini_multiplo
from .prompts import PROMPT_NORMAL, PROMPT_ANTIGO, PROMPT_MULTIPLO
from .parecer import gerar_parecer
from .merge_multiplo import mesclar_matriculas, calcular_areas, matriculas_canceladas
from .car_lookup import buscar_car_por_coordenada, parse_coordenada_livre
from .api import MultiaAPI
from .fotos import extrair_fotos_pdf, extrair_fotos_qrcode_pdf, obter_sessao_autenticada
import webview
from requests.exceptions import SSLError, ConnectionError as ReqConnError, Timeout
from .sheets import registrar_laudo, criar_pasta_matricula

def _print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))


CHECKLIST_ITENS = [
    ("capa",        "Preencher Capa"),
    ("parecer",     "Preencher e salvar parecer"),
    ("sistemas",    "Verificar Sistemas Disponíveis"),
    ("vistoria",    "Marcar não se aplica"),
    ("fotos",       "Verificar fotos"),
    ("edificacoes", "Criar edificações (se houver)"),
    ("anexos",      "Anexar arquivos"),
    ("croqui",      "Salvar Croqui"),
]
_CHECKLIST_PERMITE_NA = {"edificacoes"}


class Bridge:
    def __init__(self):
        self._window      = None   # definido após webview.create_window
        self.api          = None
        self.uuid_atual   = None
        self.pdf_path     = None
        self.dados_pdf    = None
        self.sistema      = "multia"
        self.matricula       = None   # número da matrícula do imóvel
        self.cidade_avaliacao = ""    # cidade/UF já vêm da própria avaliação (Infoel),
        self.uf_avaliacao     = ""    # antes mesmo de analisar o PDF
        self.pasta_base      = None   # pasta base configurada pelo usuário
        self.pasta_matricula = None   # pasta da matrícula atual
        self.codigo_infoel   = None   # código infoel atual
        self.aba_planilha    = None   # aba da planilha de laudos
        self.pasta_cubs      = None   # pasta dos CUBs
        self.coop            = "outra"  # cooperativa selecionada
        self.modo_multiplo    = False  # análise de múltiplas matrículas
        self.pdfs_multiplos   = []     # [{"path":..., "nome":...}] — até 10
        self.matriculas_dados = []     # dados brutos por matrícula (Gemini)
        self.analise_confrontantes = {}
        self.areas_info       = {}     # calcular_areas() — separada + soma
        self._resetar_checklist()

    # ── Utilitário interno ──────────────────────────────────────
    def _log(self, msg):
        """Envia mensagem de log para o frontend."""
        if self._window:
            safe = msg.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
            self._window.evaluate_js(f'addLog(`{safe}`)')

    def _set_btn(self, btn_id, enabled: bool, text: str = None):
        """Habilita/desabilita botão e opcionalmente muda o texto."""
        js = f'setBtn("{btn_id}", {str(enabled).lower()}'
        if text:
            safe = text.replace('"', '\\"')
            js += f', "{safe}"'
        js += ')'
        if self._window:
            self._window.evaluate_js(js)

    def _emit(self, event: str, data=None):
        """Dispara evento customizado no frontend."""
        payload = json.dumps(data) if data is not None else 'null'
        if self._window:
            self._window.evaluate_js(f'onEvent("{event}", {payload})')

    # ── Checklist de processos da revisão ───────────────────────
    def _resetar_checklist(self):
        self.checklist = {chave: {"feito": False, "origem": None, "na": False} for chave, _ in CHECKLIST_ITENS}

    def _montar_checklist(self):
        return [
            {"chave": chave, "label": label, **self.checklist.get(chave, {"feito": False, "origem": None, "na": False})}
            for chave, label in CHECKLIST_ITENS
        ]

    def _marcar_checklist(self, chave, origem="auto"):
        """Marca um item como feito (chamado internamente após uma ação ter sucesso)."""
        if chave not in self.checklist:
            return
        self.checklist[chave] = {"feito": True, "origem": origem, "na": False}
        self._emit("checklist_atualizado", {"itens": self._montar_checklist()})

    def get_checklist(self):
        return {"itens": self._montar_checklist()}

    def marcar_checklist_manual(self, chave: str):
        """Marca um item como feito manualmente — o frontend já confirmou com o usuário antes de chamar isso."""
        self._marcar_checklist(chave, origem="manual")
        return {"ok": True, "itens": self._montar_checklist()}

    def desmarcar_checklist(self, chave: str):
        if chave in self.checklist:
            self.checklist[chave] = {"feito": False, "origem": None, "na": False}
        return {"ok": True, "itens": self._montar_checklist()}

    def marcar_checklist_na(self, chave: str):
        """Alterna o estado 'não se aplica' — só permitido para itens condicionais (ex: edificações)."""
        if chave not in _CHECKLIST_PERMITE_NA:
            return {"ok": False}
        atual = self.checklist.get(chave, {"feito": False, "origem": None, "na": False})
        novo_na = not atual.get("na")
        self.checklist[chave] = {"feito": False, "origem": None, "na": novo_na}
        self._emit("checklist_atualizado", {"itens": self._montar_checklist()})
        return {"ok": True, "itens": self._montar_checklist()}

    # ── Configuração ────────────────────────────────────────────
    def get_config_inicial(self):
        """Retorna dados estáticos necessários para montar a UI."""
        return {
            "opcoes":          OPCOES,
            "obs_opcionais":   OBS_OPCIONAIS,
            "infra_itens":     INFRA_ITENS,
            "opcoes_fonte_croqui": OPCOES_FONTE_CROQUI,
            "risco_perguntas": RISCO_PERGUNTAS,
            "env_carregado":   _ENV_CARREGADO,
        }

    # ── Sistema / Autenticação ──────────────────────────────────
    def conectar(self, sistema: str):
        """Autentica no sistema escolhido (multia ou multiamais)."""
        self.sistema = sistema
        # Carrega configurações do usuário na inicialização
        self.get_config()
        if not _ENV_CARREGADO:
            self._log("⚠️ Arquivo .env não encontrado. Configure as credenciais.")
        jwt  = JWT_PADRAO_MAIS  if sistema == "multiamais" else JWT_PADRAO
        auth = AUTH_BASIC_MAIS  if sistema == "multiamais" else AUTH_BASIC
        if not jwt or not auth:
            self._log("❌ Credenciais não configuradas. Verifique o arquivo .env.")
            return {"ok": False, "msg": "Credenciais não configuradas"}
        self.api = MultiaAPI(jwt, sistema=sistema)
        nome = "MultiA Mais" if sistema == "multiamais" else "MultiA"
        self._log(f"✅ Autenticado no sistema '{nome}'")
        return {"ok": True, "nome": nome}

    # ── Busca ────────────────────────────────────────────────────
    def buscar_avaliacao(self, busca: str):
        """Busca avaliação pelo código Infoel. Se a avaliação já tiver uma
        coordenada preenchida (pelo vistoriador, no campo de coordenadas da
        própria Infoel), busca o CAR correspondente em segundo plano
        (Infoterras), sem atrasar a busca."""
        if not self.api:
            return {"ok": False, "msg": "Conecte ao sistema primeiro."}
        try:
            avs = self.api.buscar_avaliacao(busca)
            if not avs:
                self._log("⚠️ Nenhuma avaliação encontrada.")
                return {"ok": False, "msg": "Nenhuma avaliação encontrada"}
            av = next((a for a in avs if str(a.get("REG","")) == str(busca)), avs[0])
            self.uuid_atual    = av["UUID"]
            self.codigo_infoel = str(av["REG"])
            self.matricula     = str(av.get("DOCUMENTO") or av.get("documento") or "")
            self.cidade_avaliacao = str(av.get("CIDADE") or "").strip()
            self.uf_avaliacao     = str(av.get("UF") or "").strip().upper()
            self._log(f"✅ Encontrada: REG {av['REG']} | {av.get('CIDADE','?')}/{av.get('UF','?')}")
            self._log(f"   UUID: {self.uuid_atual}")
            if self.matricula:
                self._log(f"   Matrícula: {self.matricula}")
            self._resetar_checklist()
            self._emit("checklist_atualizado", {"itens": self._montar_checklist()})

            # Cria a pasta da matrícula já aqui, assim o KML do CAR e o croqui
            # (gerados mais adiante) têm onde salvar em vez de cair na pasta base.
            if self.pasta_base and self.matricula:
                self.pasta_matricula = criar_pasta_matricula(
                    self.pasta_base, self.matricula, log_fn=self._log
                )

            self._buscar_car_da_avaliacao(av.get("CIDADE", ""), av.get("UF", ""))

            return {"ok": True, "reg": av['REG'], "cidade": av.get('CIDADE','?'), "uf": av.get('UF','?'), "matricula": self.matricula}
        except Exception as ex:
            self._log(f"❌ Erro ao buscar: {ex}")
            return {"ok": False, "msg": str(ex)}

    def _buscar_car_da_avaliacao(self, cidade: str, uf: str):
        """Lê o campo de coordenadas (REG 53) já preenchido na avaliação
        pelo vistoriador, converte para graus decimais e busca o CAR
        correspondente (Infoterras), sem bloquear a busca da avaliação."""
        CAMPO_COORDENADAS = 53

        def worker():
            try:
                dados = self.api.buscar_dados_avaliacao(self.uuid_atual)
                dados_atuais = {
                    str(d.get("REGCAMPO") or d.get("REG", "")): d.get("VALOR", "")
                    for d in dados.get("dadosTipoBem", [])
                }
                texto_coord = (dados_atuais.get(str(CAMPO_COORDENADAS)) or "").strip()
                if not texto_coord or texto_coord.lower() in ("-", "null", "none"):
                    return  # vistoriador ainda não preencheu — nada a fazer, silencioso

                coords = parse_coordenada_livre(texto_coord)
                if not coords:
                    self._log(f"\n[car] ⚠️ Não consegui interpretar a coordenada da avaliação: "
                              f"'{texto_coord}'. Confira o CAR manualmente em infoterras.com.br.")
                    return
                lat, lon = coords

                if not cidade or not uf:
                    self._log("\n[car] ⚠️ Cidade/UF não identificados na avaliação — não é possível buscar o CAR.")
                    return

                self._log(f"\n[car] 🔎 Coordenada da avaliação: '{texto_coord}' → {lat:.5f}, {lon:.5f}")
                self._log(f"[car] Buscando CAR em {cidade}/{uf}...")
                resultado = buscar_car_por_coordenada(uf, cidade, lat, lon, log_fn=self._log)
                if not resultado:
                    return

                self._log(f"[car] ✅ CAR encontrado: {resultado['car']}")
                self._log(f"   Área: {resultado.get('area')} ha | Status: {resultado.get('status')} | {resultado.get('cond')}")

                pasta = self.pasta_matricula or self.pasta_base
                if pasta and resultado.get("pontos"):
                    identificador = self.matricula or self.codigo_infoel or "matricula"
                    nome_arquivo = f"Croqui_{self._sanitizar_nome_arquivo(identificador)}.kml"
                    data = gerar_kml_bytes(resultado["pontos"], str(resultado["car"]))
                    caminho = os.path.join(pasta, nome_arquivo)
                    with open(caminho, "wb") as f:
                        f.write(data)
                    self._log(f"   💾 KML salvo: {caminho}")
                elif not pasta:
                    self._log("   ⚠️ Pasta de download não configurada — KML não foi salvo (CAR já identificado acima).")
            except Exception as ex:
                import traceback
                self._log(f"[car] ❌ Erro: {ex}")
                self._log(traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()

    # ── PDF ──────────────────────────────────────────────────────
    def selecionar_pdf(self):
        """Abre diálogo de seleção de PDF via PyWebView."""
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("PDF Files (*.pdf)",)
            )
            if result and len(result) > 0:
                path = result[0]
                self.pdf_path = path
                return {"ok": True, "path": path, "nome": os.path.basename(path)}
        except Exception:
            # Fallback para tkinter
            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
            path = filedialog.askopenfilename(
                parent=root, filetypes=[("PDF", "*.pdf")], title="Selecionar PDF"
            )
            root.destroy()
            if path:
                self.pdf_path = path
                return {"ok": True, "path": path, "nome": os.path.basename(path)}
        return {"ok": False}

    def definir_pdf(self, path: str):
        """Define o PDF via drag and drop."""
        if path.lower().endswith(".pdf") and os.path.exists(path):
            self.pdf_path = path
            return {"ok": True, "nome": os.path.basename(path)}
        return {"ok": False, "msg": "Arquivo inválido ou não encontrado"}

    def analisar_pdf(self, tipo: str):
        """Envia PDF para o Gemini e extrai os dados. Executa em thread."""
        if not self.pdf_path:
            return {"ok": False, "msg": "Selecione um PDF primeiro."}

        def worker():
            try:
                self._set_btn("btn-analisar", False, "Analisando...")
                self._log(f"📖 Lendo PDF: {os.path.basename(self.pdf_path)}")
                with open(self.pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                self._log(f"   Tamanho: {len(pdf_bytes)//1024} KB")
                self._log("🤖 Enviando para Gemini — aguardando resposta...")
                dados = chamar_gemini(pdf_bytes, PROMPT_NORMAL if tipo == "normal" else PROMPT_ANTIGO)
                self.dados_pdf = dados
                self._log("✅ Gemini respondeu!\n")
                self._log("─── DADOS EXTRAÍDOS ───")
                for c in ["cri","proprietarios","endereco","numero","bairro","complemento",
                          "cidade","uf","tipo_imovel","area_total","testada","profundidade",
                          "incra","car","rodovias_acesso","cidades_vizinhas","averbacoes","unidade"]:
                    v = dados.get(c)
                    self._log(f"  {'✔' if v else '—'} {c}: {v if v else '(não encontrado)'}")

                # Exibir averbações classificadas no log (apenas laudo normal)
                av_class = dados.get("averbacoes_classificadas") if tipo == "normal" else None
                if av_class:
                    self._log("\n─── AVERBACOES CLASSIFICADAS ───")
                    # Gemini pode retornar como dict ou como lista — tratar ambos
                    if isinstance(av_class, list):
                        if av_class:
                            for item in av_class:
                                self._log(f"  • {item}")
                        else:
                            self._log("  Nenhuma averbaçao relevante encontrada")
                    else:
                        categorias = {
                            "construcoes_demoliciones": "Construcoes / Demolicoes",
                            "areas_nao_edificaveis":    "Areas Nao Edificaveis",
                            "ferrovias_gasodutos":      "Ferrovias / Gasodutos",
                            "desmembramentos":          "Desmembramentos",
                            "remembramentos":           "Remembramentos",
                            "reservas_legais":          "Reservas Legais / APP",
                        }
                        tem_alguma = False
                        for chave, rotulo in categorias.items():
                            itens = av_class.get(chave, [])
                            if itens:
                                tem_alguma = True
                                self._log(f"  {rotulo}:")
                                for item in itens:
                                    self._log(f"    • {item}")
                        if not tem_alguma:
                            self._log("  Nenhuma averbaçao relevante encontrada")
                else:
                    self._log("\n  Averbaçoes classificadas nao retornadas pelo Gemini")
                # Segunda chamada para azimutes se poucos pontos UTM
                coord = dados.get("coordenadas")
                npts_atual = len((coord or {}).get("pontos", []))
                fmt_atual  = ((coord or {}).get("formato") or "").upper()

                if fmt_atual == "UTM" and npts_atual < 3:
                    self._log(f"\n[azimutes] Poucos pontos UTM ({npts_atual}) — buscando azimutes...")
                    try:
                        res_az = chamar_gemini_azimutes(pdf_bytes)
                        azimutes = res_az.get("azimutes")
                        ponto_ini = res_az.get("ponto_inicial")
                        zona = res_az.get("zona_utm", (coord or {}).get("zona_utm", 22))
                        if azimutes and ponto_ini:
                            self._log(f"[azimutes] {len(azimutes)} trecho(s) — calculando poligono...")
                            dados["azimutes"] = azimutes
                            dados["coordenadas"] = {
                                "formato": "UTM", "zona_utm": zona, "hemisferio": "S",
                                "pontos": [{"x": float(ponto_ini["x"]), "y": float(ponto_ini["y"])}],
                            }
                            coord_calc = montar_coordenadas_de_azimutes(dados)
                            if coord_calc:
                                dados["coordenadas"] = coord_calc
                                self.dados_pdf = dados
                                self._log(f"[azimutes] Poligono calculado: {len(coord_calc['pontos'])} pontos UTM")
                            else:
                                self._log("[azimutes] Nao foi possivel calcular o poligono")
                        else:
                            self._log("[azimutes] Nenhum azimute encontrado")
                    except Exception as ex_az:
                        self._log(f"[azimutes] Erro: {ex_az}")

                coord = dados.get("coordenadas")
                if coord and coord.get("pontos"):
                    fmt  = coord.get("formato","?")
                    npts = len(coord["pontos"])
                    origem = " (via azimutes)" if coord.get("origem") == "azimutes" else ""
                    self._log(f"\n Coordenadas: {npts} pontos | Formato: {fmt}{origem}")
                else:
                    self._log("\n Coordenadas nao encontradas.")

                # KML a partir das coordenadas da própria matrícula — salvo sempre que
                # houver coordenadas, independente do CAR (Infoterras) ter sido encontrado ou não.
                if coord and coord.get("pontos"):
                    pasta_kml = self.pasta_matricula or self.pasta_base
                    if pasta_kml:
                        try:
                            pontos_kml   = normalizar_pontos(coord)
                            data_kml     = gerar_kml_bytes(pontos_kml, self._nome())
                            identificador = self.matricula or self.codigo_infoel or "matricula"
                            nome_arq_kml = f"Croqui_{self._sanitizar_nome_arquivo(identificador)}.kml"
                            caminho_kml  = os.path.join(pasta_kml, nome_arq_kml)
                            with open(caminho_kml, "wb") as f:
                                f.write(data_kml)
                            self._log(f"\n🗺️ KML salvo: {caminho_kml}")
                        except Exception as ex_kml:
                            self._log(f"\n⚠️ Erro ao gerar KML: {ex_kml}")
                    else:
                        self._log("\n⚠️ Coordenadas encontradas, mas pasta de download não configurada — KML não foi salvo.")

                if tipo == "antigo":
                    grupos = dados.get("grupos_vistoria",[])
                    self._log(f"\n📦 Grupos de vistoria: {len(grupos)}")
                    for g in grupos:
                        self._log(f"   {'📷' if g.get('tem_foto') else '📋'} {g.get('nome','?')} | Área: {g.get('area') or '—'}")
                # Log vaga de garagem (campo retornado pelo Gemini)
                _tem_vaga = dados.get("vaga_garagem") is True
                self._log(f"\n🚗 Vaga de garagem: {'Sim — mencionada na matrícula' if _tem_vaga else 'Não mencionada'}")

                # Croqui do polígono real (a partir das confrontações; usa os ângulos do
                # documento quando informados, senão assume canto reto e sinaliza no desenho)
                if tipo == "normal":
                    resultado_croqui = calcular_poligono_confrontacoes(
                        dados.get("confrontacoes"), dados.get("angulos_internos")
                    )
                    if resultado_croqui:
                        pontos_croqui, aproximado = resultado_croqui
                        pasta = self.pasta_matricula or self.pasta_base
                        if pasta:
                            try:
                                svg = gerar_svg_confrontacoes(
                                    pontos_croqui, dados.get("confrontacoes") or {},
                                    distancia_esquina=dados.get("distancia_esquina"),
                                    numero_matricula=self.codigo_infoel or "",
                                    aproximado=aproximado,
                                )
                                nome_arq = f"croqui_{self._sanitizar_nome_arquivo(self.codigo_infoel or 'matricula')}.svg"
                                caminho = os.path.join(pasta, nome_arq)
                                with open(caminho, "w", encoding="utf-8") as f:
                                    f.write(svg)
                                aviso = " (ângulo(s) assumido(s) reto — não informado no documento)" if aproximado else ""
                                self._log(f"\n🖼️ Croqui do polígono salvo{aviso}: {caminho}")
                            except Exception as ex_svg:
                                self._log(f"\n⚠️ Erro ao gerar croqui: {ex_svg}")
                        else:
                            self._log("\n⚠️ Croqui calculado, mas pasta de download não configurada — não foi salvo.")
                    else:
                        self._log("\n🖼️ Croqui: medidas das confrontações insuficientes para desenhar — não gerado.")

                self._log("\n✅ Análise concluída!")
                self._emit("analise_concluida", {
                    "tipo": tipo,
                    "tem_coords": bool(coord and coord.get("pontos")),
                    "coords_utm": bool(coord and coord.get("formato","").upper() == "UTM"),
                    "dados": dados,
                    "tem_cidade": bool(dados.get("cidade")),
                })

                # Exibir análise jurídica no log (complemento das averbações)
                analise_jur = dados.get("analise_juridica")
                if analise_jur and isinstance(analise_jur, list) and analise_jur:
                    self._log("\n─── ANÁLISE JURÍDICA ───")
                    for item in analise_jur:
                        if item.get("cancelada"):
                            continue
                        id_av   = item.get("id_av", "?")
                        subtipo = item.get("subtipo", "")
                        impacto = item.get("impacto", "")
                        desc    = item.get("descricao", "")
                        credora = item.get("credora")
                        icone   = "🔴" if impacto == "Alto" else "🟡" if impacto == "Médio" else "🟢"
                        linha   = f"  {icone} {id_av} — {subtipo}: {desc}"
                        if credora:
                            linha += f" | Credora: {credora}"
                        self._log(linha)
                    # Alerta de negociabilidade
                    tem_alto = any(i.get("impacto") == "Alto" for i in analise_jur if not i.get("cancelada"))
                    if tem_alto:
                        self._log("  ⚠️ Fator potencialmente prejudicial à negociabilidade identificado")
                elif analise_jur == []:
                    self._log("\n  ✔ Não foram identificados ônus ou restrições relevantes com potencial impacto negativo à negociabilidade.")

                # Alerta de matrícula cancelada/encerrada
                mat_cancelada = dados.get("matricula_cancelada") or {}
                if mat_cancelada.get("cancelada"):
                    self._log("\n🚨🚨🚨 ATENÇÃO: MATRÍCULA CANCELADA/ENCERRADA 🚨🚨🚨")
                    self._log(f"   {mat_cancelada.get('motivo') or 'Confira o documento — indício de cancelamento encontrado.'}")

                # Registrar na planilha e criar pasta da matrícula
                tipo_imovel = dados.get("tipo_imovel", "URBANO")
                creds_path  = self._credentials_path()
                if creds_path and self.codigo_infoel and self.matricula and self.aba_planilha:
                    self._log("\n[sheets] Registrando na planilha...")
                    registrar_laudo(creds_path, self.codigo_infoel,
                                    self.matricula, tipo_imovel, log_fn=self._log,
                                    aba_nome=self.aba_planilha)
                elif not creds_path:
                    self._log("\n[sheets] credentials.json não encontrado — planilha não atualizada")
                elif not self.matricula:
                    self._log("\n[sheets] Matrícula não encontrada — planilha não atualizada")
                elif not self.aba_planilha:
                    self._log("\n[sheets] Aba da planilha não configurada — planilha não atualizada")

                if self.pasta_base and self.matricula:
                    self.pasta_matricula = criar_pasta_matricula(
                        self.pasta_base, self.matricula, log_fn=self._log
                    )

                self._salvar_analise(dados, tipo)
            except (SSLError, ReqConnError) as ex:
                self._log(f"❌ Erro de conexão: {ex}")
                self._log("   💡 Verifique sua conexão ou tente novamente.")
            except Timeout as ex:
                self._log(f"❌ Timeout: {ex}")
                self._log("   💡 PDF muito grande ou rede lenta. Tente novamente.")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())
            finally:
                self._set_btn("btn-analisar", True, "Analisar PDF com Gemini")

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    # ── Múltiplas matrículas ─────────────────────────────────────
    def ativar_modo_multiplo(self, ativo: bool):
        """Liga/desliga o modo de análise de várias matrículas juntas."""
        self.modo_multiplo = bool(ativo)
        self.pdfs_multiplos   = []
        self.matriculas_dados = []
        self.analise_confrontantes = {}
        self.areas_info = {}
        self.dados_pdf = None
        if ativo:
            # Evita reaproveitar a pasta de uma matrícula única analisada antes,
            # nessa mesma sessão, como destino do KML da mesclagem.
            self.pasta_matricula = None
        return {"ok": True}

    def adicionar_pdfs_multiplos(self):
        """Abre diálogo de seleção múltipla de PDF, respeitando o limite de 10."""
        MAX_PDFS = 10
        vagas = MAX_PDFS - len(self.pdfs_multiplos)
        if vagas <= 0:
            return {"ok": False, "msg": f"Limite de {MAX_PDFS} PDFs atingido.",
                    "pdfs": [p["nome"] for p in self.pdfs_multiplos]}
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, allow_multiple=True, file_types=("PDF Files (*.pdf)",)
            )
        except Exception:
            root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
            result = filedialog.askopenfilenames(parent=root, filetypes=[("PDF", "*.pdf")], title="Selecionar PDFs")
            root.destroy()
        if not result:
            return {"ok": False, "pdfs": [p["nome"] for p in self.pdfs_multiplos]}
        novos = list(result)[:vagas]
        ignorados = len(result) - len(novos)
        for caminho in novos:
            self.pdfs_multiplos.append({"path": caminho, "nome": os.path.basename(caminho)})
        msg = f"{ignorados} PDF(s) não foram adicionados — limite de {MAX_PDFS} atingido." if ignorados > 0 else None
        return {"ok": True, "pdfs": [p["nome"] for p in self.pdfs_multiplos], "msg": msg}

    def remover_pdf_multiplo(self, idx: int):
        if 0 <= idx < len(self.pdfs_multiplos):
            self.pdfs_multiplos.pop(idx)
        return {"ok": True, "pdfs": [p["nome"] for p in self.pdfs_multiplos]}

    def analisar_multiplas_matriculas(self):
        """Envia todos os PDFs adicionados numa única chamada ao Gemini,
        mescla os dados (mesma lógica da MultiA Central) e deixa o resultado
        em self.dados_pdf, no mesmo formato de uma análise de 1 PDF só —
        assim capa, vistoria e fotos continuam funcionando sem mudança."""
        if not self.pdfs_multiplos:
            return {"ok": False, "msg": "Adicione ao menos um PDF."}

        def worker():
            try:
                self._set_btn("btn-analisar", False, "Analisando...")
                self._log(f"📖 Lendo {len(self.pdfs_multiplos)} PDF(s)...")
                lista_bytes = []
                for p in self.pdfs_multiplos:
                    with open(p["path"], "rb") as f:
                        lista_bytes.append(f.read())

                self._log("🤖 Enviando para o Gemini — aguardando resposta...")
                resposta = chamar_gemini_multiplo(lista_bytes, PROMPT_MULTIPLO)
                self._processar_resposta_multipla(resposta, checar_qtd_pdfs=True)
            except (SSLError, ReqConnError) as ex:
                self._log(f"❌ Erro de conexão: {ex}")
            except Timeout as ex:
                self._log(f"❌ Timeout: {ex}")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())
            finally:
                self._set_btn("btn-analisar", True, "Analisar PDF com Gemini")

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def _processar_resposta_multipla(self, resposta: dict, checar_qtd_pdfs: bool):
        """Processa o resultado da análise múltipla — mescla os dados, calcula
        áreas, registra na planilha, cria a pasta combinada e gera os croquis.
        Usado tanto pela chamada real ao Gemini quanto pelo JSON colado manualmente.
        """
        self.matriculas_dados = resposta.get("matriculas", [])
        self.analise_confrontantes = resposta.get("analise_confrontantes") or {}

        self._log(f"✅ {len(self.matriculas_dados)} matrícula(s) analisada(s):")
        for m in self.matriculas_dados:
            self._log(f"   • {m.get('numero_matricula','?')} — {m.get('cidade','?')}/{m.get('uf','?')}"
                      f" — {m.get('area_total','?')}")

        if checar_qtd_pdfs and len(self.matriculas_dados) != len(self.pdfs_multiplos):
            self._log(
                f"\n⚠️⚠️⚠️ ATENÇÃO: você enviou {len(self.pdfs_multiplos)} PDF(s), mas a análise retornou "
                f"{len(self.matriculas_dados)} matrícula(s). Confira se algum PDF ficou de fora "
                f"e repita a análise se necessário."
            )

        if len(self.matriculas_dados) > 1:
            conf = self.analise_confrontantes or {}
            explicacao = conf.get("explicacao", "")
            if conf.get("sao_confrontantes"):
                self._log("\n✅✅✅ CONFRONTANTES ✅✅✅")
                self._log(f"   {explicacao}")
            else:
                self._log("\n⚠️⚠️⚠️ NÃO CONFRONTANTES ⚠️⚠️⚠️")
                self._log(f"   {explicacao}")

        # Alerta de matrícula(s) cancelada(s)/encerrada(s)
        canceladas = matriculas_canceladas(self.matriculas_dados)
        for item in canceladas:
            self._log(f"\n🚨🚨🚨 ATENÇÃO: MATRÍCULA {item['numero']} CANCELADA/ENCERRADA 🚨🚨🚨")
            self._log(f"   {item.get('motivo') or 'Confira o documento — indício de cancelamento encontrado.'}")

        # Área — sempre mostra separada por matrícula e a soma
        self.areas_info = calcular_areas(self.matriculas_dados)
        self._log(f"\n─── ÁREAS ───")
        for item in self.areas_info["itens"]:
            self._log(f"   Matrícula {item['numero']}: {item['area_raw']}")
        self._log(f"   SOMA: {self.areas_info['soma']:.4f} {self.areas_info['unidade']}".replace(".", ","))

        self.dados_pdf = mesclar_matriculas(self.matriculas_dados)
        self._log("\n✅ Análise concluída — pode preencher a capa, gerar o parecer e o croqui.")
        soma_fmt = f"{self.areas_info['soma']:.4f}".replace(".", ",") + " " + self.areas_info["unidade"]
        self._emit("analise_multipla_concluida", {
            "dados": self.dados_pdf,
            "area_total_soma": soma_fmt,
            "matriculas": [
                {"numero": m.get("numero_matricula"), "area": m.get("area_total")}
                for m in self.matriculas_dados
            ],
        })
        self._set_btn("btn-kml",      True)
        self._set_btn("btn-parecer",  True)
        self._set_btn("btn-capa",     True)
        self._set_btn("btn-sistemas", True)

        # Registrar na planilha e criar pasta com as matrículas combinadas
        matriculas_str = " - ".join(str(m.get("numero_matricula") or "?") for m in self.matriculas_dados)
        tipo_imovel = self.dados_pdf.get("tipo_imovel", "URBANO")
        creds_path  = self._credentials_path()
        if creds_path and self.codigo_infoel and self.aba_planilha:
            self._log("\n[sheets] Registrando na planilha...")
            registrar_laudo(creds_path, self.codigo_infoel,
                            matriculas_str, tipo_imovel, log_fn=self._log,
                            aba_nome=self.aba_planilha)
        elif not creds_path:
            self._log("\n[sheets] credentials.json não encontrado — planilha não atualizada")
        elif not self.aba_planilha:
            self._log("\n[sheets] Aba da planilha não configurada — planilha não atualizada")

        if self.pasta_base:
            self.pasta_matricula = criar_pasta_matricula(
                self.pasta_base, matriculas_str, log_fn=self._log
            )

        self._salvar_analise(resposta, "multiplo")

        # Croqui do polígono real por matrícula (usa os ângulos do documento quando
        # informados, senão assume canto reto e sinaliza no desenho)
        pasta_svg = self.pasta_matricula or self.pasta_base
        for m in self.matriculas_dados:
            numero = m.get("numero_matricula", "?")
            resultado_croqui = calcular_poligono_confrontacoes(
                m.get("confrontacoes"), m.get("angulos_internos")
            )
            if not resultado_croqui:
                continue
            pontos_croqui, aproximado = resultado_croqui
            if not pasta_svg:
                self._log(f"\n⚠️ Croqui da matrícula {numero} calculado, mas pasta de download não configurada — não foi salvo.")
                continue
            try:
                svg = gerar_svg_confrontacoes(
                    pontos_croqui, m.get("confrontacoes") or {},
                    distancia_esquina=m.get("distancia_esquina"),
                    numero_matricula=str(numero),
                    aproximado=aproximado,
                )
                nome_arq = f"croqui_{self._sanitizar_nome_arquivo(numero)}.svg"
                caminho = os.path.join(pasta_svg, nome_arq)
                with open(caminho, "w", encoding="utf-8") as f:
                    f.write(svg)
                aviso = " (ângulo(s) assumido(s) reto)" if aproximado else ""
                self._log(f"\n🖼️ Croqui salvo{aviso} (matrícula {numero}): {caminho}")
            except Exception as ex_svg:
                self._log(f"\n⚠️ Erro ao gerar croqui da matrícula {numero}: {ex_svg}")

    def processar_json_manual_multiplo(self, json_str: str):
        """Processa um JSON colado manualmente no formato da análise múltipla
        (mesmo formato do PROMPT_MULTIPLO: {matriculas: [...], analise_confrontantes: {...}})."""
        import json as _json
        import re as _re
        try:
            clean = _re.sub(r"```json\s*", "", json_str)
            clean = _re.sub(r"```\s*", "", clean).strip()
            s = clean.find("{"); e = clean.rfind("}") + 1
            if s == -1:
                return {"ok": False, "msg": "JSON inválido — não encontrado objeto { }"}
            resposta = _json.loads(clean[s:e])
            self._processar_resposta_multipla(resposta, checar_qtd_pdfs=False)
            return {"ok": True}
        except Exception as ex:
            import traceback
            self._log(f"❌ Erro: {ex}")
            self._log(traceback.format_exc())
            return {"ok": False, "msg": str(ex)}

    def carregar_analise_salva(self):
        """Carrega a análise salva anteriormente para a matrícula atual (se
        existir), sem chamar o Gemini de novo. Veja _salvar_analise."""
        pasta = self.pasta_matricula or self.pasta_base
        if not pasta:
            return {"ok": False, "msg": "Pasta da matrícula não encontrada. Busque o código ou analise um PDF primeiro."}

        caminho = os.path.join(pasta, "_analise_mpa.json")
        if not os.path.exists(caminho):
            return {"ok": False, "msg": "Nenhuma análise salva encontrada para essa matrícula."}

        try:
            with open(caminho, "r", encoding="utf-8") as f:
                salvo = json.load(f)
            tipo  = salvo.get("tipo", "normal")
            dados = salvo.get("dados")
            if not dados:
                return {"ok": False, "msg": "Arquivo de análise salva está vazio ou corrompido."}

            self._log(f"\n📂 Carregando análise salva ({tipo})...")
            if tipo == "multiplo":
                self._processar_resposta_multipla(dados, checar_qtd_pdfs=False)
            else:
                self.processar_json_manual(json.dumps(dados), tipo)
            self._log("✅ Análise salva carregada — nenhuma chamada ao Gemini foi feita.")
            return {"ok": True, "tipo": tipo}
        except Exception as ex:
            import traceback
            self._log(f"❌ Erro ao carregar análise salva: {ex}")
            self._log(traceback.format_exc())
            return {"ok": False, "msg": str(ex)}

    # ── KML / SHP ────────────────────────────────────────────────
    def gerar_kml(self):
        if self.modo_multiplo:
            return self._gerar_kml_multiplo()
        if not self.dados_pdf:
            return {"ok": False, "msg": "Analise o PDF primeiro."}
        coord = self.dados_pdf.get("coordenadas")
        if not coord:
            return {"ok": False, "msg": "Sem coordenadas."}
        try:
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
            pasta = result[0] if result and len(result) > 0 else None
        except Exception:
            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
            pasta = filedialog.askdirectory(parent=root, title="Pasta para KML")
            root.destroy()
        if not pasta:
            return {"ok": False}
        def worker():
            try:
                self._log("🗺️ Gerando KML...")
                pontos = normalizar_pontos(coord)
                self._log(f"   {len(pontos)} pontos convertidos para WGS84")
                nome = self._nome()
                data = gerar_kml_bytes(pontos, nome)
                # Salvar na pasta da matrícula se configurada, senão na pasta escolhida
                destino = self.pasta_matricula if self.pasta_matricula else pasta
                path = os.path.join(destino, nome + ".kml")
                with open(path, "wb") as f:
                    f.write(data)
                self._log(f"✅ KML salvo: {path}")
            except Exception as ex:
                self._log(f"❌ Erro KML: {ex}")
        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def _gerar_kml_multiplo(self):
        if not self.matriculas_dados:
            return {"ok": False, "msg": "Analise as matrículas primeiro."}
        try:
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
            pasta = result[0] if result and len(result) > 0 else None
        except Exception:
            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
            pasta = filedialog.askdirectory(parent=root, title="Pasta para KML")
            root.destroy()
        if not pasta:
            return {"ok": False}

        def worker():
            try:
                self._log("🗺️ Gerando KML (múltiplas matrículas)...")
                poligonos = []
                faltando = []
                for m in self.matriculas_dados:
                    numero = m.get("numero_matricula", "?")
                    coord = m.get("coordenadas")
                    if not coord or not coord.get("pontos"):
                        faltando.append(numero)
                        continue
                    pontos = normalizar_pontos(coord)
                    poligonos.append({"pontos": pontos, "label": numero})

                if faltando:
                    self._log(f"⚠️ Sem coordenadas, não entraram no KML: {', '.join(faltando)}")
                if not poligonos:
                    self._log("❌ Nenhuma matrícula com coordenadas — KML não foi gerado.")
                    return

                data = gerar_kml_multiplo_uniao(poligonos)
                nomes = "_".join(self._sanitizar_nome_arquivo(p["label"]) for p in poligonos)
                nome_arquivo = f"KML_{nomes}.kml" if len(nomes) < 100 else f"KML_{len(poligonos)}_matriculas.kml"
                destino = self.pasta_matricula if self.pasta_matricula else pasta
                path = os.path.join(destino, nome_arquivo)
                with open(path, "wb") as f:
                    f.write(data)
                self._log(f"✅ KML salvo: {path}")
                if len(poligonos) > 1:
                    self._log("   Inclui o polígono 'Área Somada' com a união de todas as matrículas.")
                self._marcar_checklist("croqui")
            except ImportError as ex:
                self._log(f"❌ Erro KML: biblioteca necessária não instalada ({ex}). Rode: pip install shapely")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro KML: {ex}")
                self._log(traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def gerar_shp(self):
        if not self.dados_pdf:
            return {"ok": False, "msg": "Analise o PDF primeiro."}
        coord = self.dados_pdf.get("coordenadas")
        if not coord or coord.get("formato","").upper() != "UTM":
            return {"ok": False, "msg": "SHP apenas para coordenadas UTM."}
        try:
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
            pasta = result[0] if result and len(result) > 0 else None
        except Exception:
            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
            pasta = filedialog.askdirectory(parent=root, title="Pasta para SHP")
            root.destroy()
        if not pasta:
            return {"ok": False}
        def worker():
            try:
                self._log("📦 Gerando SHP...")
                zona = int(coord.get("zona_utm") or 22)
                path = gerar_shp(coord["pontos"], zona, self._nome(), pasta)
                self._log(f"✅ SHP salvo: {path}")
            except Exception as ex:
                self._log(f"❌ Erro SHP: {ex}")
        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    # ── Capa ─────────────────────────────────────────────────────
    def preencher_capa(self, tipo_laudo: str):
        if not self.api or not self.uuid_atual:
            return {"ok": False, "msg": "Conecte e busque a avaliação primeiro."}
        if not self.dados_pdf:
            return {"ok": False, "msg": "Analise o PDF primeiro."}
        # Para laudo antigo, pede as obs antes de prosseguir
        if tipo_laudo == "antigo":
            return {"ok": True, "pedir_obs": True}
        self._executar_preencher_capa(tipo_laudo, [], "")
        return {"ok": True}

    def preencher_capa_com_obs(self, tipo_laudo: str, obs_selecionadas: list, obs_adicional: str):
        """Preenche capa do laudo antigo com observações informadas pelo usuário."""
        if not self.api or not self.uuid_atual:
            return {"ok": False, "msg": "Conecte e busque a avaliação primeiro."}
        if not self.dados_pdf:
            return {"ok": False, "msg": "Analise o PDF primeiro."}
        self._executar_preencher_capa(tipo_laudo, obs_selecionadas, obs_adicional)
        return {"ok": True}

    def _executar_preencher_capa(self, tipo_laudo: str, obs_selecionadas: list, obs_adicional: str):
        """Worker interno de preenchimento de capa."""
        def worker():
            try:
                self._log("\n📋 Preenchendo campos da capa...")
                self.api.salvar_campos_capa(self.uuid_atual, self.dados_pdf,
                                            log_fn=self._log, tipo_laudo=tipo_laudo)
                if tipo_laudo == "antigo" and self.dados_pdf.get("parecer"):
                    self._log("\n📝 Laudo antigo — salvando parecer com observações...")
                    parecer = self.dados_pdf["parecer"].strip()
                    paragrafos = [p.strip() for p in re.split(r"\n{2,}", parecer) if p.strip()]
                    parecer_txt = "\n\n".join(paragrafos)
                    # Montar obs finais
                    from .config import OBS_OPCIONAIS
                    obs_partes = []
                    obs_fixa = ("Obs.1: Para efeitos da avaliação, o imóvel foi considerado livre "
                                    "de penhoras, arrestos, hipotecas, contaminação do solo ou ônus de qualquer natureza.")
                    obs_partes.append(obs_fixa)
                    obs_num = 2
                    for idx in obs_selecionadas:
                        if 0 <= idx < len(OBS_OPCIONAIS):
                            obs_partes.append(f"Obs.{obs_num}: {OBS_OPCIONAIS[idx]}")
                            obs_num += 1
                    if obs_adicional:
                        obs_partes.append(f"Obs.{obs_num}: {obs_adicional}")
                    parecer_final = parecer_txt + "\n\n" + "\n".join(obs_partes)
                    self.api.salvar_parecer(self.uuid_atual, parecer_final, log_fn=self._log)
                    self._log("✅ Parecer salvo!")
                    self._marcar_checklist("parecer")
                self._log("✅ Capa preenchida com sucesso!")
                self._marcar_checklist("capa")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())
        threading.Thread(target=worker, daemon=True).start()

    # ── Parecer ──────────────────────────────────────────────────
    def get_dados_parecer(self):
        """Retorna dados do PDF para pré-preencher o formulário de parecer."""
        d = self.dados_pdf or {}
        return {
            "unidade":         d.get("unidade",""),
            "complemento":     d.get("complemento",""),
            "coordenadas_raw": d.get("coordenadas_raw",""),
            "fonte_croqui":    d.get("fonte_croqui",""),
            "posicao":         d.get("posicao",""),
            "rua_esquina":     d.get("rua_esquina",""),
            "area_total":      d.get("area_total",""),
            "tipo_imovel":     d.get("tipo_imovel","URBANO"),
        }

    def _montar_texto_area_multipla(self) -> str:
        """Monta 'X, sendo Y da matrícula 123 e Z da matrícula 124' para o
        parecer, no modo de múltiplas matrículas (a área nunca vai pra capa,
        só aparece nesse texto)."""
        unidade = self.areas_info["unidade"]
        soma_fmt = f"{self.areas_info['soma']:.4f}".replace(".", ",") + f" {unidade}"
        partes = [
            f"{item['area_num']:.4f}".replace(".", ",") + f" {unidade} da matrícula {item['numero']}"
            for item in self.areas_info["itens"]
        ]
        if len(partes) == 1:
            sufixo = partes[0]
        elif len(partes) == 2:
            sufixo = f"{partes[0]} e {partes[1]}"
        else:
            sufixo = ", ".join(partes[:-1]) + " e " + partes[-1]
        return f"{soma_fmt}, sendo {sufixo}"

    def salvar_parecer(self, opcoes: dict, tipo_laudo: str):
        """Gera e salva o parecer no sistema."""
        if not self.api or not self.uuid_atual:
            return {"ok": False, "msg": "Conecte e busque a avaliação primeiro."}
        if not self.dados_pdf:
            return {"ok": False, "msg": "Analise o PDF primeiro."}

        def worker():
            try:
                self._log("\n📝 Gerando parecer...")
                if tipo_laudo == "antigo" and self.dados_pdf.get("parecer"):
                    parecer = self.dados_pdf["parecer"].strip()
                    paragrafos = [p.strip() for p in re.split(r"\n{2,}", parecer) if p.strip()]
                    parecer = "\n\n".join(paragrafos)
                    obs_fixa = ("Obs.1: Para efeitos da avaliação, o imóvel foi considerado livre "
                                "de penhoras, arrestos, hipotecas, contaminação do solo ou ônus de qualquer natureza.")
                    parecer += "\n\n" + obs_fixa
                    self._log("   Usando parecer extraído do laudo antigo")
                else:
                    opcoes_parecer = opcoes
                    if self.modo_multiplo and self.areas_info and self.areas_info.get("itens"):
                        opcoes_parecer = dict(opcoes)
                        opcoes_parecer["area_total_texto"] = self._montar_texto_area_multipla()
                    parecer = gerar_parecer(self.dados_pdf, opcoes_parecer)
                    self._log("   Parecer gerado com as opções selecionadas")
                self._log(f"   Tamanho: {len(parecer)} caracteres")
                self._log("\n─── PRÉVIA ───")
                for l in parecer.split("\n")[:6]:
                    self._log("  " + l)
                self._log("  [...]")
                self._log("\n☁️ Salvando parecer no sistema...")
                self.api.salvar_parecer(self.uuid_atual, parecer, log_fn=self._log)
                self._log("✅ Parecer salvo com sucesso!")
                self._marcar_checklist("parecer")
                self._processar_divisoes_e_extras(opcoes)
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro parecer: {ex}")
                self._log(traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}


    # ── Sistemas de Consulta ──────────────────────────────────────
    def buscar_sistemas_consulta(self):
        """Busca sistemas disponíveis para a cidade/UF do imóvel atual."""
        SPREADSHEET_SISTEMAS = "1IZAMNvgR6P1p2F_gYacRkhl7xR84UnM8Fb_Mayzxfyw"

        d      = self.dados_pdf or {}
        # Cidade/UF já vêm da própria avaliação (Infoel) assim que o código é buscado —
        # usa o que o PDF confirmar, e cai para a da avaliação se ainda não analisou.
        cidade = (d.get("cidade") or "").strip() or self.cidade_avaliacao
        uf     = ((d.get("uf") or "").strip() or self.uf_avaliacao).upper()
        tipo   = d.get("tipo_imovel", "URBANO").upper()

        if not cidade or not uf:
            return {"ok": False, "msg": "Analise um PDF primeiro para identificar a cidade e UF."}

        try:
            from .sheets import _obter_token
            import requests as _req
            from urllib.parse import quote as _quote

            creds_path = self._credentials_path()
            if not creds_path:
                return {"ok": False, "msg": "credentials.json não encontrado."}

            token   = _obter_token(creds_path)
            headers = {"Authorization": f"Bearer {token}"}
            sistemas = []

            def _url(range_str):
                return (f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_SISTEMAS}"
                        f"/values/{_quote(range_str, safe='!:')}")

            # Sistemas por cidade — busca A1:H para incluir o cabeçalho com nomes das colunas
            # Busca sem limite de coluna — linha 1 é título mesclado, linha 2 é o cabeçalho real
            cidade_data = _req.get(_url("SISTEMAS POR CIDADE!A1:Z"), headers=headers, timeout=30).json().get("values", [])
            cab  = cidade_data[1] if len(cidade_data) > 1 else []  # linha 2 = cabeçalho real
            rows = cidade_data[2:]                                   # dados a partir da linha 3
            for row in rows:
                if len(row) < 2: continue
                if str(row[0]).strip().upper() != uf: continue
                if str(row[1]).strip().lower() != cidade.lower(): continue

                # Colunas fixas: C(2)=geo link, D(3)=login geo, E(4)=senha geo
                geo = row[2].strip() if len(row) > 2 and row[2] else ""
                if geo and geo != "-":
                    nome_geo = cab[2].strip() if len(cab) > 2 else "Georreferenciamento"
                    sistemas.append({"tipo": nome_geo, "link": geo,
                                     "login": row[3].strip() if len(row) > 3 else "-",
                                     "senha": row[4].strip() if len(row) > 4 else "-"})

                # F(5)=espelho, G(6)=inundação — fixas, sem login
                for col_idx in (5, 6):
                    val = row[col_idx].strip() if len(row) > col_idx and row[col_idx] else ""
                    if val and val != "-":
                        nome = cab[col_idx].strip() if len(cab) > col_idx else f"Sistema col {col_idx}"
                        sistemas.append({"tipo": nome, "link": val, "login": "-", "senha": "-"})

                # H(7) em diante — colunas novas, sem login/senha
                for col_idx in range(7, len(cab)):
                    val = row[col_idx].strip() if len(row) > col_idx and row[col_idx] else ""
                    if val and val != "-":
                        nome = cab[col_idx].strip() if cab[col_idx] else f"Sistema col {col_idx + 1}"
                        sistemas.append({"tipo": nome, "link": val, "login": "-", "senha": "-"})
                break

            # Sistemas por estado
            rows_e = _req.get(_url("SISTEMAS POR ESTADO!A3:E"), headers=headers, timeout=30).json().get("values", [])
            for row in rows_e:
                if not row or not row[0]: continue
                if str(row[0]).strip().upper() != uf: continue
                sistemas.append({
                    "tipo":    str(row[1]).strip() if len(row) > 1 else "Sistema Estadual",
                    "link":    str(row[2]).strip() if len(row) > 2 else "",
                    "login":   str(row[3]).strip() if len(row) > 3 else "-",
                    "senha":   str(row[4]).strip() if len(row) > 4 else "-",
                    "estadual": True,
                })

            # Sistemas rurais globais
            if tipo == "RURAL":
                rows_r = _req.get(_url("SISTEMAS RURAIS GLOBAIS!A3:D"), headers=headers, timeout=30).json().get("values", [])
                for row in rows_r:
                    if not row or not row[0]: continue
                    sistemas.append({
                        "tipo":  str(row[0]).strip(),
                        "link":  str(row[1]).strip() if len(row) > 1 else "",
                        "login": str(row[2]).strip() if len(row) > 2 else "-",
                        "senha": str(row[3]).strip() if len(row) > 3 else "-",
                        "rural": True,
                    })

            self._marcar_checklist("sistemas")
            return {"ok": True, "cidade": cidade, "uf": uf, "tipo": tipo, "sistemas": sistemas}

        except Exception as ex:
            return {"ok": False, "msg": str(ex)}


    def _processar_divisoes_e_extras(self, opcoes: dict):
        tipo_imovel = (self.dados_pdf or {}).get("tipo_imovel","URBANO").upper()
        unid_padrao = "ha" if tipo_imovel == "RURAL" else "m²"

        def _tof(v):
            if not v: return 0.0
            n = re.sub(r"[^\d,.]","",str(v)).strip()
            if re.match(r"^\d{1,3}(\.\d{3})+,\d+$",n): n=n.replace(".","").replace(",",".")
            else: n=n.replace(",",".")
            try: return float(n)
            except: return 0.0

        def _criar_grupo(nome, area_str, unidade):
            self._log(f"   ➕ Criando grupo '{nome}' — {area_str} {unidade}...")
            self.api.adicionar_grupo(self.uuid_atual, nome)
            vs = self.api.buscar_vistoria(self.uuid_atual)
            gs = vs.get('grupos') or vs.get('gruposImoveis') or []
            if not gs: self._log("   ⚠️ Grupo não retornado"); return
            reg = max(g['REG'] for g in gs)

            obs_val = " " if "edificacao" not in nome.lower() else ""

            self.api.editar_grupo(self.uuid_atual, reg, nome=nome,
                                  area=area_str.replace(",","."),
                                  unidade=unidade, obs=obs_val, nao_se_aplica=False)
            self._log(f"   ✔ REG={reg} salvo")

        def _criar_divisoes(divisoes, area_total_raw, prefixo=""):
            if not divisoes:
                return
            area_total_f = _tof(area_total_raw)
            nomes = list(divisoes.keys())
            if len(nomes) == 1:
                _criar_grupo(prefixo + nomes[0], normalizar_area(area_total_raw) or "0", unid_padrao)
            else:
                soma   = sum(_tof(v) for v in divisoes.values() if v)
                vazios = [n for n,v in divisoes.items() if not v]
                if len(vazios) == 1 and area_total_f > 0:
                    divisoes[vazios[0]] = f"{(area_total_f-soma):.4f}".replace(".",",")
                for nome, val in divisoes.items():
                    area_str = normalizar_area(val) if val else normalizar_area(area_total_raw) or "0"
                    _criar_grupo(prefixo + nome, area_str, unid_padrao)

        modo_area = opcoes.get("modo_area", "somar")

        if self.modo_multiplo and modo_area == "dividir":
            self._log("\n🌿 Criando grupos de divisões de área (por matrícula)...")
            divisoes_por_matricula = opcoes.get("divisoes_por_matricula") or {}
            if not divisoes_por_matricula:
                self._log("\n⚠️ Nenhuma divisão de área selecionada")
            for numero, divisoes_mat in divisoes_por_matricula.items():
                m = next((mm for mm in self.matriculas_dados if str(mm.get("numero_matricula")) == str(numero)), None)
                area_mat = (m or {}).get("area_total", "")
                self._log(f"\n  Matrícula {numero} — área {area_mat}")
                _criar_divisoes(dict(divisoes_mat), area_mat, prefixo=f"Mat.{numero} - ")
        else:
            divisoes = opcoes.get("divisoes_area", {})
            if self.modo_multiplo and self.areas_info:
                area_total_raw = f"{self.areas_info['soma']:.4f}".replace(".", ",")
            else:
                area_total_raw = (self.dados_pdf or {}).get("area_total","")
            if divisoes:
                self._log("\n🌿 Criando grupos de divisões de área...")
                _criar_divisoes(divisoes, area_total_raw)
            else:
                self._log("\n⚠️ Nenhuma divisão de área selecionada")

        coord_manual = opcoes.get("coordenadas_manual","").strip()
        if coord_manual:
            self._log(f"\n📍 Salvando coordenadas: {coord_manual}")
            self.api.salvar_campo(self.uuid_atual, 53, coord_manual)
            self._log("   ✔ Coordenadas salvas")

        fonte = opcoes.get("fonte_croqui","").strip()
        if fonte:
            self._log(f"\n🗺️ Salvando fonte do croqui...")
            self.api.salvar_campo(self.uuid_atual, 60, fonte)
            self._log("   ✔ Fonte do croqui salva")

    # ── Vistoria ─────────────────────────────────────────────────
    def validar_vistoria(self):
        if not self.api or not self.uuid_atual:
            return {"ok": False, "msg": "Conecte e busque a avaliação primeiro."}
        def worker():
            try:
                self._log("\n✅ Iniciando validação da vistoria...")
                ja = self.api.validar_vistoria_nao_aplica(self.uuid_atual, log_fn=self._log)
                self._log("─"*40)
                if ja:
                    self._log(f"⚠️ {len(ja)} grupo(s) tinham valor preenchido antes:")
                    import datetime
                    linhas = [f"Validação — {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
                              f"UUID: {self.uuid_atual}", "─"*50]
                    for item in ja:
                        self._log(f"  📌 {item['nome']}")
                        linhas.append(f"\n{item['nome']}")
                        if item.get('area') and item['area'] not in ("","0","0.00"):
                            linhas.append(f"  Área: {item['area']} m²")
                        if item.get('valor') and item['valor'] not in ("","0","0.00"):
                            linhas.append(f"  Valor: {item['valor']}")
                        if item.get('obs') and item['obs'].lower() not in ("","não se aplica","nao se aplica"):
                            linhas.append(f"  Obs: {item['obs']}")
                    # Salvar na pasta da matrícula se disponível
                    if self.pasta_matricula and os.path.exists(self.pasta_matricula):
                        pasta = self.pasta_matricula
                    elif self.pdf_path:
                        pasta = os.path.dirname(self.pdf_path)
                    else:
                        pasta = os.path.expanduser("~")
                    nome_arq = f"vistoria_{self.uuid_atual[:8]}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    caminho = os.path.join(pasta, nome_arq)
                    with open(caminho, "w", encoding="utf-8") as f:
                        f.write("\n".join(linhas))
                    self._log(f"\n💾 Salvo em: {caminho}")
                else:
                    self._log("ℹ️ Nenhum grupo tinha valor preenchido.")
                self._log("\n✅ Todos marcados como 'Não se aplica'!")
                self._marcar_checklist("vistoria")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())
        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def importar_laudo(self):
        if not self.api or not self.uuid_atual:
            return {"ok": False, "msg": "Conecte e busque a avaliação primeiro."}
        if not self.dados_pdf or not self.pdf_path:
            return {"ok": False, "msg": "Analise o PDF primeiro."}
        grupos = self.dados_pdf.get("grupos_vistoria",[])
        if not grupos:
            return {"ok": False, "msg": "Nenhum grupo encontrado no PDF."}

        self._log("\n🔍 Extraindo fotos para pré-visualização...")
        hdrs        = {k:v for k,v in self.api.headers.items()}
        dl_session  = obter_sessao_autenticada(sistema=self.sistema, log_fn=self._log)
        fotos       = extrair_fotos_qrcode_pdf(self.pdf_path, jwt_headers=hdrs,
                                               log_fn=self._log, sistema=self.sistema,
                                               download_session=dl_session)
        if not fotos:
            self._log("   ⚠️ Tentando extração direta...")
            fotos = extrair_fotos_pdf(self.pdf_path)

        grupos_area = [g for g in grupos
                       if normalizar_area(str(g.get('area') or '').strip())
                       not in ('','0','0,00','null','None')]
        n_fotos = len(fotos)
        self._log(f"   📷 {n_fotos} foto(s) encontrada(s)")

        # Retorna resumo para o frontend confirmar
        return {
            "ok": True,
            "grupos_area": len(grupos_area),
            "n_fotos": n_fotos,
            "fotos_nomes": [f.get('nome','?') for f in fotos],
        }

    def confirmar_importar_com_obs(self, obs_selecionadas: list, obs_adicional: str):
        """Executa a importação com observações do laudo antigo."""
        # Salva as obs para uso posterior no parecer se necessário
        self._obs_antigo = {"obs_selecionadas": obs_selecionadas, "obs_adicional": obs_adicional}
        return self.confirmar_importar()

    def confirmar_importar(self):
        """Executa a importação após confirmação do usuário no frontend."""
        if not self.dados_pdf or not self.pdf_path:
            return {"ok": False}
        grupos      = self.dados_pdf.get("grupos_vistoria",[])
        hdrs        = {k:v for k,v in self.api.headers.items()}
        dl_session  = obter_sessao_autenticada(sistema=self.sistema, log_fn=self._log)
        fotos       = extrair_fotos_qrcode_pdf(self.pdf_path, jwt_headers=hdrs,
                                               log_fn=self._log, sistema=self.sistema,
                                               download_session=dl_session)
        if not fotos:
            fotos = extrair_fotos_pdf(self.pdf_path)

        grupos_area = [g for g in grupos
                       if normalizar_area(str(g.get('area') or '').strip())
                       not in ('','0','0,00','null','None')]

        def worker():
            try:
                self._log(f"\n📥 Iniciando importação...")
                def _criar_grupo_reg(nome):
                    self._log(f"   ➕ Criando grupo '{nome}'...")
                    self.api.adicionar_grupo(self.uuid_atual, nome)
                    vs = self.api.buscar_vistoria(self.uuid_atual)
                    gs = vs.get('grupos') or vs.get('gruposImoveis') or []
                    if not gs: return None
                    reg = max(g['REG'] for g in gs)
                    self._log(f"   ✔ REG={reg}")
                    return reg

                for i, grupo in enumerate(grupos_area):
                    nome     = _title_grupo(grupo.get('nome', f'Grupo {i+1}'))
                    area_val = normalizar_area(str(grupo.get('area') or '').strip())
                    unidade  = str(grupo.get('unidade_area') or 'm²').strip()
                    self._log(f"\n  [área {i+1}/{len(grupos_area)}] '{nome}' — {area_val} {unidade}")
                    reg = _criar_grupo_reg(nome)
                    if not reg: continue
                    # Espaço nas obs quando há apenas 1 grupo de área (força tabela na plataforma)
                    obs_grupo = str(grupo.get('obs') or '').strip()
                    if len(grupos_area) == 1 and not obs_grupo:
                        obs_grupo = " "
                    self.api.editar_grupo(self.uuid_atual, reg, nome=nome,
                        construcao=grupo.get('construcao','N'),
                        averbado=grupo.get('averbado','N'),
                        area=area_val, unidade=unidade,
                        valor_unidade=str(grupo.get('valor_unidade') or ''),
                        obs=obs_grupo, nao_se_aplica=False)
                    self._log(f"   ✔ Área salva")

                n_fotos = len(fotos)
                if fotos:
                    self._log(f"\n  [fotos] Criando grupo 'Fotos' com {n_fotos} foto(s)...")
                    reg_fotos = _criar_grupo_reg('Fotos')
                    if reg_fotos:
                        self.api.editar_grupo(self.uuid_atual, reg_fotos,
                            nome='Fotos', construcao='N', averbado='N',
                            area='0', valor_unidade='', obs='', nao_se_aplica=True)
                        fotos_enviadas = 0
                        for j, foto in enumerate(fotos):
                            nome_foto = foto.get('nome') or f'Foto {j+1}'
                            fn = f"foto_{j+1}.{foto['ext']}"
                            self._log(f"   📷 [{j+1}/{n_fotos}] '{nome_foto}'")
                            try:
                                resp = self.api.salvar_imagem_grupo(self.uuid_atual, reg_fotos, foto['bytes'], fn)
                                fotos_enviadas += 1
                                reg_img = (resp.get('dados',{}).get('REG') or resp.get('REG') or
                                           resp.get('dados',{}).get('reg'))
                                if not reg_img:
                                    vs2  = self.api.buscar_vistoria(self.uuid_atual)
                                    imgs = [im for im in (vs2.get('imagens') or [])
                                            if str(im.get('REGGRUPO') or im.get('regGrupo','')) == str(reg_fotos)]
                                    if imgs: reg_img = max(im.get('REG') or im.get('reg') for im in imgs)
                                if reg_img:
                                    self.api.editar_descricao_imagem(self.uuid_atual, reg_fotos, reg_img, nome_foto)
                                    self._log(f"     ✔ '{nome_foto}'")
                            except Exception as ex_f:
                                self._log(f"   ⚠️ Erro foto {j+1}: {ex_f}")
                        self._log(f"   ✅ {fotos_enviadas}/{n_fotos} foto(s) enviada(s)")
                self._log(f"\n✅ Importação concluída!")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}


    # ── Edificações ──────────────────────────────────────────────
    def get_dados_edificacao(self):
        """Retorna dados do PDF para pré-preencher o formulário de edificação."""
        d = self.dados_pdf or {}
        return {
            "unidade":          d.get("unidade", ""),
            "complemento":      d.get("complemento", ""),
            "pavimento":        d.get("pavimento", ""),
            "areas_privativas": d.get("areas_privativas", []),
        }

    def criar_edificacoes(self, edificacoes: list):
        """Cria todas as edificações na API. Executa em thread."""
        if not self.api or not self.uuid_atual:
            return {"ok": False, "msg": "Conecte e busque a avaliação primeiro."}
        if not edificacoes:
            return {"ok": False, "msg": "Nenhuma edificação para criar."}

        def worker():
            try:
                self._log(f"\n🏠 Criando {len(edificacoes)} edificação(ões)...")
                for i, ed in enumerate(edificacoes):
                    nome     = ed.get("nome", f"Edificação {i+1}")
                    averbado = ed.get("averbado", "N")
                    area     = ed.get("area", "0")
                    obs      = ed.get("obs", "")
                    self._log(f"\n  [{i+1}/{len(edificacoes)}] '{nome}' — {area} m² | {'Averbada' if averbado=='S' else 'Não Averbada'}")
                    self._log(f"   ➕ Criando grupo...")
                    self.api.adicionar_grupo(self.uuid_atual, nome)
                    vs = self.api.buscar_vistoria(self.uuid_atual)
                    gs = vs.get("grupos") or vs.get("gruposImoveis") or []
                    if not gs:
                        self._log("   ⚠️ Grupo não retornado pela API")
                        continue
                    reg = max(g["REG"] for g in gs)
                    self._log(f"   ✔ REG={reg}")
                    area_api = str(area).replace(",", ".")
                    self.api.editar_grupo(
                        self.uuid_atual, reg,
                        nome=nome,
                        construcao="S",
                        averbado=averbado,
                        area=area_api,
                        unidade="m²",
                        valor_unidade="",
                        obs=obs,
                        nao_se_aplica=False
                    )
                    self._log(f"   ✔ Edificação salva")
                self._log(f"\n✅ {len(edificacoes)} edificação(ões) criada(s) com sucesso!")
                self._marcar_checklist("edificacoes")
            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def gerar_texto_edificacao(self, campos: dict) -> str:
        """Gera o texto da obs da edificação a partir dos campos do formulário."""
        modelo = campos.get("modelo", "padrao")
        if modelo == "privativo":
            return _gerar_obs_privativo(campos)
        return _gerar_obs_padrao(campos)

    # ── Drag and Drop ────────────────────────────────────────────
    def handle_file_drop(self, path: str):
        """Chamado pelo main quando um arquivo é arrastado para a janela."""
        path = path.strip()
        if path.lower().endswith('.pdf') and os.path.exists(path):
            self.pdf_path = path
            nome = os.path.basename(path)
            safe = nome.replace("'", "\'")
            if self._window:
                self._window.evaluate_js(f"carregarPDF('{safe}')")
            return {"ok": True, "nome": nome}
        return {"ok": False}

    # ── Configuração de pasta ────────────────────────────────────
    def selecionar_pasta_base(self):
        """Abre diálogo para selecionar a pasta base e salva no .env local."""
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.FOLDER,
                allow_multiple=False
            )
            pasta = result[0] if result and len(result) > 0 else None
        except Exception:
            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
            pasta = filedialog.askdirectory(parent=root, title="Selecionar pasta base dos laudos")
            root.destroy()
        if pasta:
            self.pasta_base = pasta
            # Salvar no arquivo de configuração local
            config_path = self._config_path()
            try:
                cfg = {}
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                cfg["pasta_base"] = pasta
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                self._log(f"[pasta] Pasta base configurada: {pasta}")
            except Exception as ex:
                self._log(f"[pasta] Erro ao salvar configuração: {ex}")
            return {"ok": True, "pasta": pasta}
        return {"ok": False}

    def get_config(self):
        """Retorna configurações atuais."""
        config_path = self._config_path()
        cfg = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
        self.pasta_base   = cfg.get("pasta_base", "")
        self.aba_planilha = cfg.get("aba_planilha", "")
        self.pasta_cubs   = cfg.get("pasta_cubs", "")
        return {
            "pasta_base":   self.pasta_base   or "",
            "aba_planilha": self.aba_planilha or "",
            "pasta_cubs":   self.pasta_cubs   or "",
        }

    def salvar_config_texto(self, chave: str, valor: str):
        """Salva uma configuração de texto no multia_config.json."""
        config_path = self._config_path()
        try:
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg[chave] = valor.strip()
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            # Atualizar em memória
            if chave == "aba_planilha": self.aba_planilha = valor.strip()
            if chave == "pasta_cubs":   self.pasta_cubs   = valor.strip()
            self._log(f"[config] {chave} salvo: {valor.strip()}")
            return {"ok": True}
        except Exception as ex:
            return {"ok": False, "msg": str(ex)}

    def selecionar_pasta_cubs(self):
        """Abre diálogo para selecionar a pasta dos CUBs."""
        try:
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
            pasta = result[0] if result and len(result) > 0 else None
        except Exception:
            root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
            pasta = filedialog.askdirectory(parent=root, title="Selecionar pasta dos CUBs")
            root.destroy()
        if pasta:
            r = self.salvar_config_texto("pasta_cubs", pasta)
            if r["ok"]:
                return {"ok": True, "pasta": pasta}
        return {"ok": False}

    def _exe_dir(self):
        """Retorna a pasta onde está o executável ou o script, corretamente para --onefile."""
        import sys
        if getattr(sys, 'frozen', False):
            # Rodando como .exe gerado pelo PyInstaller
            return os.path.dirname(sys.executable)
        else:
            # Rodando como script Python
            return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _config_path(self):
        """Caminho do arquivo de configuração local."""
        return os.path.join(self._exe_dir(), "multia_config.json")

    def _credentials_path(self):
        """Caminho do credentials.json do Service Account."""
        path = os.path.join(self._exe_dir(), "credentials.json")
        return path if os.path.exists(path) else None


    # ── JSON Manual ───────────────────────────────────────────────
    def get_prompt(self, tipo: str) -> str:
        """Retorna o prompt que seria enviado ao Gemini."""
        from .prompts import PROMPT_NORMAL, PROMPT_ANTIGO, PROMPT_MULTIPLO
        if tipo == "multiplo":
            return PROMPT_MULTIPLO
        return PROMPT_NORMAL if tipo == "normal" else PROMPT_ANTIGO

    def processar_json_manual(self, json_str: str, tipo: str):
        """Processa um JSON colado manualmente, igual ao retorno do Gemini."""
        import json as _json
        try:
            # Limpar markdown se houver
            import re as _re
            clean = _re.sub(r"```json\s*", "", json_str)
            clean = _re.sub(r"```\s*", "", clean).strip()
            s = clean.find("{"); e = clean.rfind("}") + 1
            if s == -1:
                return {"ok": False, "msg": "JSON inválido — não encontrado objeto { }"}
            dados = _json.loads(clean[s:e])
            self.dados_pdf = dados

            # Processar coordenadas por azimutes se necessário
            coord = dados.get("coordenadas")
            npts  = len((coord or {}).get("pontos", []))
            fmt   = ((coord or {}).get("formato") or "").upper()
            if fmt == "UTM" and npts < 3:
                from .gemini import chamar_gemini_azimutes
                from .coordenadas import montar_coordenadas_de_azimutes
                self._log("[azimutes] Poucos pontos — verificando azimutes no JSON...")
                azimutes  = dados.get("azimutes")
                ponto_ini = dados.get("ponto_inicial")
                if azimutes and ponto_ini:
                    dados["coordenadas"] = {
                        "formato": "UTM", "zona_utm": (coord or {}).get("zona_utm", 22),
                        "hemisferio": "S",
                        "pontos": [{"x": float(ponto_ini["x"]), "y": float(ponto_ini["y"])}],
                    }
                    coord_calc = montar_coordenadas_de_azimutes(dados)
                    if coord_calc:
                        dados["coordenadas"] = coord_calc
                        self.dados_pdf = dados
                        self._log(f"[azimutes] Polígono calculado: {len(coord_calc['pontos'])} pontos")

            coord = dados.get("coordenadas")
            self._emit("analise_concluida", {
                "tipo":       tipo,
                "tem_coords": bool(coord and coord.get("pontos")),
                "coords_utm": bool(coord and (coord.get("formato","").upper() == "UTM")),
                "dados":      dados,
                "tem_cidade": bool(dados.get("cidade")),
            })

            # Registrar planilha e criar pasta
            tipo_imovel = dados.get("tipo_imovel", "URBANO")
            creds_path  = self._credentials_path()
            if creds_path and self.codigo_infoel and self.matricula and self.aba_planilha:
                from .sheets import registrar_laudo, criar_pasta_matricula
                registrar_laudo(creds_path, self.codigo_infoel,
                                self.matricula, tipo_imovel, log_fn=self._log,
                                aba_nome=self.aba_planilha)
            elif not self.aba_planilha:
                self._log("\n[sheets] Aba da planilha não configurada — planilha não atualizada")
            if self.pasta_base and self.matricula:
                from .sheets import criar_pasta_matricula
                self.pasta_matricula = criar_pasta_matricula(
                    self.pasta_base, self.matricula, log_fn=self._log)

            self._log_dados_extraidos(dados, tipo)
            # Log vaga de garagem (campo do JSON)
            _tem_vaga_j = dados.get("vaga_garagem") is True
            self._log(f"\n🚗 Vaga de garagem: {'Sim — mencionada na matrícula' if _tem_vaga_j else 'Não mencionada'}")
            self._salvar_analise(dados, tipo)
            self._log("\n✅ JSON processado com sucesso!")
            return {"ok": True}

        except _json.JSONDecodeError as ex:
            return {"ok": False, "msg": f"JSON inválido: {ex}"}
        except Exception as ex:
            import traceback
            return {"ok": False, "msg": f"{ex}\n{traceback.format_exc()}"}


    # ── Anexar Croqui e Arquivos ──────────────────────────────────
    def anexar_croqui_e_arquivos(self):
        """Salva PDF da matrícula, anexa CUB, centro e fotos do croqui."""
        if not self.api or not self.uuid_atual:
            return {"ok": False, "msg": "Conecte e busque a avaliação primeiro."}
        if not self.pasta_matricula or not os.path.exists(self.pasta_matricula):
            return {"ok": False, "msg": "Pasta da matrícula não encontrada. Analise o PDF primeiro."}
        if not self.pasta_cubs:
            return {"ok": False, "msg": "Pasta de CUBs não configurada. Configure-a em Configurações antes de continuar."}

        PASTA_CUBS = self.pasta_cubs
        uf = (self.dados_pdf or {}).get("uf", "").upper().strip()

        def worker():
            try:
                import shutil as _shutil
                import datetime

                self._log("\n📎 Iniciando anexação de arquivos...")

                # ── 0. Renomear anexos existentes ──────────────────
                try:
                    dados_av = self.api.buscar_dados_avaliacao(self.uuid_atual)
                    arquivos_existentes = dados_av.get("arquivos", [])
                    for arq in arquivos_existentes:
                        if arq.get("TIPO") != "BEM":
                            continue
                        reg_arq   = arq.get("REG")
                        desc_atual = str(arq.get("DESCRICAO") or "").strip()
                        if not reg_arq or not desc_atual:
                            continue
                        desc_lower = desc_atual.lower()
                        if any(p in desc_lower for p in ("mat", "matricula", "matrícula")):
                            novo_nome = "MATRÍCULA"
                        else:
                            novo_nome = desc_atual.upper()
                        if novo_nome != desc_atual:
                            self.api.renomear_anexo(reg_arq, novo_nome)
                            self._log(f"   ✔ Renomeado: '{desc_atual}' → '{novo_nome}'")
                except Exception as _er:
                    self._log(f"   ⚠️ Erro ao renomear anexos: {_er}")

                # ── 1. Salvar cópia do PDF da matrícula na pasta ──
                if self.pdf_path and os.path.exists(self.pdf_path):
                    nome_pdf = os.path.basename(self.pdf_path)
                    dest_pdf = os.path.join(self.pasta_matricula, nome_pdf)
                    if not os.path.exists(dest_pdf):
                        _shutil.copy2(self.pdf_path, dest_pdf)
                        self._log(f"   ✔ PDF da matrícula salvo: {nome_pdf}")
                    else:
                        self._log(f"   ℹ️ PDF já existe na pasta: {nome_pdf}")
                else:
                    self._log("   ⚠️ PDF da matrícula não encontrado")

                # ── 2. Anexar CUB ──────────────────────────────────
                if uf:
                    cub_path = os.path.join(PASTA_CUBS, f"CUB - {uf}.pdf")
                    if os.path.exists(cub_path):
                        mtime    = os.path.getmtime(cub_path)
                        dt_modif = datetime.datetime.fromtimestamp(mtime)
                        hoje     = datetime.datetime.now()
                        if dt_modif.year < hoje.year or dt_modif.month < hoje.month:
                            self._log(f"   ⚠️ CUB - {uf} pode estar desatualizado "
                                      f"(última modificação: {dt_modif.strftime('%d/%m/%Y')})")
                        with open(cub_path, "rb") as f:
                            cub_bytes = f.read()
                        self.api.anexar_arquivo(self.uuid_atual, cub_bytes, f"CUB - {uf}.pdf", "CUB")
                        self._log(f"   ✔ CUB - {uf} anexado")
                    else:
                        self._log(f"   ⚠️ CUB do estado {uf} não encontrado")
                else:
                    self._log("   ⚠️ UF não identificada — CUB não anexado")

                # ── 2b. Anexar assinatura (Sicoob Campos Novos / SMO) ──
                if self.coop in ("camposnovos", "smo"):
                    assin_path = None
                    if os.path.exists(PASTA_CUBS):
                        for fname in os.listdir(PASTA_CUBS):
                            if "assinatura" in fname.lower() and fname.lower().endswith(".pdf"):
                                assin_path = os.path.join(PASTA_CUBS, fname)
                                break
                    if assin_path:
                        with open(assin_path, "rb") as f:
                            assin_bytes = f.read()
                        self.api.anexar_arquivo(
                            self.uuid_atual, assin_bytes,
                            os.path.basename(assin_path), "ASSINATURA ÁREA IDEAL"
                        )
                        self._log(f"   ✔ Assinatura anexada: {os.path.basename(assin_path)}")
                    else:
                        self._log(f"   ⚠️ Arquivo de assinatura não encontrado na pasta dos CUBs")

                # ── 3. Anexar arquivo(s) "car"/"demonstrativo" como CAR ──
                car_paths = []
                for fname in os.listdir(self.pasta_matricula):
                    nome_sem_ext = os.path.splitext(fname)[0].lower()
                    if not fname.lower().endswith(".pdf"):
                        continue
                    if "car" in nome_sem_ext or "demonstrativo" in nome_sem_ext:
                        car_paths.append(os.path.join(self.pasta_matricula, fname))
                if car_paths:
                    for path in car_paths:
                        with open(path, "rb") as f:
                            car_bytes = f.read()
                        self.api.anexar_arquivo(self.uuid_atual, car_bytes,
                                                os.path.basename(path), "CAR")
                        self._log(f"   ✔ '{os.path.basename(path)}' anexado como 'CAR'")
                else:
                    self._log("   ⚠️ Arquivo com 'car' ou 'demonstrativo' no nome não encontrado na pasta")

                # ── 4. Anexar arquivo "centro" ──────────────────────
                centro_path = None
                for fname in os.listdir(self.pasta_matricula):
                    nome_sem_ext = os.path.splitext(fname)[0].lower()
                    if "centro" in nome_sem_ext and fname.lower().endswith(".pdf"):
                        centro_path = os.path.join(self.pasta_matricula, fname)
                        break
                if centro_path:
                    with open(centro_path, "rb") as f:
                        centro_bytes = f.read()
                    self.api.anexar_arquivo(self.uuid_atual, centro_bytes,
                                            os.path.basename(centro_path), "TRAJETO ATÉ O CENTRO")
                    self._log("   ✔ Arquivo 'centro' anexado como 'TRAJETO ATÉ O CENTRO'")
                else:
                    self._log("   ⚠️ Arquivo com 'centro' no nome não encontrado na pasta")

                # ── 5. Anexar arquivos por palavra-chave ───────────
                MAPA_PALAVRAS = {
                    "bci":                 "BCI",
                    "geo":                 "SISTEMA DE INFORMAÇÕES MUNICIPAIS GEORREFERENCIADAS",
                    "georreferenciamento": "SISTEMA DE INFORMAÇÕES MUNICIPAIS GEORREFERENCIADAS",
                    "iptu":                "IPTU",
                    "hidrografia":         "HIDROGRAFIA",
                    "inundacao":           "MAPA DE INUNDAÇÃO",
                    "inundação":           "MAPA DE INUNDAÇÃO",
                }
                EXTENSOES_BUSCA = {".pdf", ".jpg", ".jpeg", ".png"}
                descricoes_usadas = set()
                for fname in os.listdir(self.pasta_matricula):
                    nome_sem_ext = os.path.splitext(fname)[0].lower()
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in EXTENSOES_BUSCA:
                        continue
                    descricao = None
                    for palavra, desc in MAPA_PALAVRAS.items():
                        if palavra in nome_sem_ext:
                            descricao = desc
                            break
                    if not descricao:
                        continue
                    if descricao in descricoes_usadas:
                        continue
                    fpath = os.path.join(self.pasta_matricula, fname)
                    with open(fpath, "rb") as f:
                        fbytes = f.read()
                    mime = "application/pdf" if ext == ".pdf" else "image/jpeg"
                    self.api.anexar_arquivo(self.uuid_atual, fbytes, fname, descricao)
                    descricoes_usadas.add(descricao)
                    self._log(f"   ✔ '{fname}' anexado como '{descricao}'")

                # ── 6. Excluir fotos existentes do croqui ─────────
                try:
                    dados_croqui = self.api.buscar_dados_avaliacao(self.uuid_atual)
                    arquivos_todos = dados_croqui.get("arquivos", [])
                    imgs_croqui = [a for a in arquivos_todos if a.get("TIPO") == "BEM"
                                   and a.get("REGGRUPO") is None
                                   and a.get("DESCRICAO","").upper() not in
                                   ("CUB","TRAJETO ATÉ O CENTRO","MATRÍCULA","BCI",
                                    "SISTEMA DE INFORMAÇÕES MUNICIPAIS GEORREFERENCIADAS",
                                    "IPTU","HIDROGRAFIA","MAPA DE INUNDAÇÃO","CAR")]
                    # Busca imagens do croqui via endpoint de vistoria
                    dados_vist = self.api.buscar_vistoria(self.uuid_atual)
                    imgs_croqui_reg = []
                    for arq in (dados_vist.get("imagens") or []):
                        if arq.get("REGGRUPO") is None:
                            imgs_croqui_reg.append(arq.get("REG") or arq.get("reg"))
                    for reg_img in imgs_croqui_reg:
                        if reg_img:
                            self.api.remover_imagem_croqui(self.uuid_atual, reg_img)
                            self._log(f"   ✔ Foto croqui {reg_img} removida")
                except Exception as _ec:
                    self._log(f"   ⚠️ Erro ao limpar croqui: {_ec}")

                # ── 7. Fotos do croqui (1 a 6) ────────────────────
                EXTENSOES_IMG = {".jpg", ".jpeg", ".png"}
                fotos_enviadas = 0
                for i in range(1, 7):
                    foto_path = None
                    for ext in EXTENSOES_IMG:
                        candidato = os.path.join(self.pasta_matricula, f"{i}{ext}")
                        if os.path.exists(candidato):
                            foto_path = candidato
                            break
                    if foto_path:
                        with open(foto_path, "rb") as f:
                            foto_bytes = f.read()
                        self.api.salvar_foto_croqui(self.uuid_atual, foto_bytes, os.path.basename(foto_path))
                        self._log(f"   ✔ Foto {i} enviada ao croqui")
                        fotos_enviadas += 1

                if fotos_enviadas == 0:
                    self._log("   ⚠️ Nenhuma foto (1 a 6) encontrada na pasta")
                else:
                    self._log(f"   ✔ {fotos_enviadas} foto(s) enviada(s) ao croqui")

                self._log("\n✅ Anexação concluída!")
                self._marcar_checklist("anexos")
                self._marcar_checklist("croqui")

            except Exception as ex:
                import traceback
                self._log(f"❌ Erro: {ex}")
                self._log(traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def set_coop(self, coop: str) -> None:
        """Salva a cooperativa selecionada para uso no anexar_croqui_e_arquivos."""
        self.coop = coop or "outra"

    def get_analise_juridica(self) -> dict:
        """Retorna análise jurídica para exibição no modal do parecer."""
        dados = self.dados_pdf or {}
        analise = dados.get("analise_juridica") or []
        # Filtra apenas vigentes
        vigentes = [i for i in analise if isinstance(i, dict) and not i.get("cancelada")]
        return {"ok": True, "itens": vigentes}

    def get_fotos_vistoria(self) -> dict:
        """Retorna todas as fotos da vistoria agrupadas por grupo."""
        try:
            if not self.uuid_atual:
                return {"ok": False, "erro": "Nenhuma avaliação carregada."}
            vs = self.api.buscar_vistoria(self.uuid_atual)

            gs   = vs.get("grupos") or vs.get("gruposImoveis") or []
            imgs = vs.get("imagens") or []

            # Mapear REG do grupo → nome
            reg_to_nome = {}
            for g in gs:
                reg  = str(g.get("REG") or g.get("reg") or "")
                nome = (g.get("NOME") or g.get("nome") or g.get("Nome") or f"Grupo {reg}").strip()
                if reg:
                    reg_to_nome[reg] = nome

            itens = []
            for img in imgs:
                reg_img   = img.get("REG")   or img.get("reg")
                reg_grupo = img.get("REGGRUPO") or img.get("regGrupo") or img.get("REG_GRUPO")
                descricao = (img.get("DESCRICAO") or img.get("descricao") or
                             img.get("Descricao") or img.get("nome") or "Sem nome").strip()
                if not reg_img or not reg_grupo:
                    continue
                grupo_nome = reg_to_nome.get(str(reg_grupo), f"Grupo {reg_grupo}")
                itens.append({
                    "reg_img":   reg_img,
                    "reg_grupo": reg_grupo,
                    "grupo":     grupo_nome,
                    "nome":      descricao,
                })

            return {"ok": True, "itens": itens}
        except Exception as e:
            return {"ok": False, "erro": str(e)}

    def renomear_fotos_selecionadas(self, fotos: list, novo_nome_global: str) -> dict:
        """Renomeia as fotos selecionadas.
        Cada item de fotos pode ter 'nome_individual' — se preenchido, usa ele.
        Se novo_nome_global estiver preenchido, sobrescreve todos."""
        try:
            if not self.uuid_atual:
                return {"ok": False, "erro": "Nenhuma avaliação carregada."}
            total = 0
            for f in fotos:
                try:
                    nome = novo_nome_global.strip() if novo_nome_global.strip() else f.get("nome_individual", "").strip()
                    if not nome:
                        continue
                    self.api.editar_descricao_imagem(
                        self.uuid_atual, f["reg_grupo"], f["reg_img"], nome)
                    self._log(f"   ✔ REG {f['reg_img']} → '{nome}'")
                    total += 1
                except Exception as ef:
                    self._log(f"   ⚠️ Erro REG {f.get('reg_img')}: {ef}")
            if total > 0:
                self._marcar_checklist("fotos")
            return {"ok": True, "total": total}
        except Exception as e:
            return {"ok": False, "erro": str(e)}

    def abrir_link(self, url: str):
        """Abre um link no navegador padrão do sistema."""
        import webbrowser
        webbrowser.open(url)
        return {"ok": True}

    # ── Helpers ──────────────────────────────────────────────────
    def _nome(self):
        d   = self.dados_pdf or {}
        raw = f"{d.get('endereco','croqui')}_{d.get('numero','')}".strip("_")
        return re.sub(r"[^\w\-]","_", raw)[:60] or "croqui"

    @staticmethod
    def _sanitizar_nome_arquivo(valor) -> str:
        """Sanitiza um valor (ex: número de matrícula) para uso seguro em nome
        de arquivo/pasta no Windows — remove quebra de linha e caracteres inválidos.
        Campos como o "DOCUMENTO" do Infoel podem trazer mais de uma matrícula
        separadas por quebra de linha; isso vira " - " em vez de quebrar o caminho.
        """
        texto = str(valor)
        texto = re.sub(r"[\r\n]+", " - ", texto)
        texto = texto.replace("/", "-").replace("\\", "-")
        texto = re.sub(r'[<>:"|?*]', "", texto)
        return re.sub(r"\s+", " ", texto).strip()

    def _com_cidade_uf_preenchidos(self, dados: dict, tipo: str) -> dict:
        """Preenche cidade/UF com os valores da própria avaliação (Infoel)
        quando o PDF não trouxer esses campos claramente — sem alterar os
        dados em memória (self.dados_pdf / self.matriculas_dados), só a cópia
        que vai para o arquivo salvo.
        """
        if not self.cidade_avaliacao and not self.uf_avaliacao:
            return dados
        if tipo == "multiplo":
            copia = dict(dados)
            novas = []
            for m in (dados.get("matriculas") or []):
                m2 = dict(m)
                if not m2.get("cidade"):
                    m2["cidade"] = self.cidade_avaliacao
                if not m2.get("uf"):
                    m2["uf"] = self.uf_avaliacao
                novas.append(m2)
            copia["matriculas"] = novas
            return copia
        copia = dict(dados)
        if not copia.get("cidade"):
            copia["cidade"] = self.cidade_avaliacao
        if not copia.get("uf"):
            copia["uf"] = self.uf_avaliacao
        return copia

    def _salvar_analise(self, dados: dict, tipo: str) -> None:
        """Salva o resultado de uma análise na pasta da matrícula, para poder
        recarregar depois (botão "Carregar Análise Salva") sem chamar o Gemini
        de novo. Silencioso se não houver pasta configurada — não é um passo
        crítico do fluxo.
        """
        pasta = self.pasta_matricula or self.pasta_base
        if not pasta:
            return
        try:
            dados_para_salvar = self._com_cidade_uf_preenchidos(dados, tipo)
            caminho = os.path.join(pasta, "_analise_mpa.json")
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump({"tipo": tipo, "dados": dados_para_salvar}, f, ensure_ascii=False, indent=2)
        except Exception as ex:
            self._log(f"\n⚠️ Erro ao salvar análise para reaproveitar depois: {ex}")


# ──────────────────────────────────────────────────────────────
# GERAÇÃO DE TEXTO DAS EDIFICAÇÕES
# ──────────────────────────────────────────────────────────────
_GENERO_FEMININO = {"casa","sala","loja","unidade","cobertura","vaga","residência","geminada"}

def _genero(unidade: str):
    """Retorna ('a','situada') ou ('o','situado') conforme a unidade."""
    primeira = (unidade or "").lower().split()[0] if unidade else ""
    if primeira in _GENERO_FEMININO:
        return "a", "situada"
    return "o", "situado"


def _gerar_obs_padrao(c: dict) -> str:
    """Gera o texto obs para edificação no modelo padrão."""
    partes = []

    # Material + constituição
    material  = c.get("material", "")
    comodos   = c.get("comodos", {})   # {nome: quantidade}
    externos  = c.get("externos", [])  # lista de strings

    # Linha de material
    if material:
        mat_lower = material.lower()
        mat_texto = "material misto" if mat_lower == "mista" else mat_lower
        partes.append(f"Em {mat_texto}")

    # Cômodos internos
    ordem_comodos = [
        "dormitório","suíte","BWC","lavabo","sala de estar","sala de jantar",
        "copa","cozinha","área de serviço","despensa","sacada","varanda",
        "área de festas com churrasqueira","garagem coberta"
    ]
    itens_comodos = []
    for nome in ordem_comodos:
        qtd = comodos.get(nome)
        if qtd:
            try:
                n = int(qtd)
                _SEM_NUM = {
                    "sala de estar","sala de jantar","copa","cozinha",
                    "despensa","sacada","varanda","garagem coberta",
                    "área de festas com churrasqueira"
                }
                if n == 1:
                    if nome in _SEM_NUM:
                        itens_comodos.append(nome)
                    else:
                        itens_comodos.append(f"1 {nome}")
                else:
                    # Pluralizar básico
                    _PLURAIS = {
                        "dormitório":                    "dormitórios",
                        "suíte":                         "suítes",
                        "BWC":                           "BWC's",
                        "lavabo":                        "lavabos",
                        "sala de estar":                 "salas de estar",
                        "sala de jantar":                "salas de jantar",
                        "copa":                          "copas",
                        "cozinha":                       "cozinhas",
                        "área de serviço":               "áreas de serviço",
                        "despensa":                      "despensas",
                        "sacada":                        "sacadas",
                        "varanda":                       "varandas",
                        "área de festas com churrasqueira": "áreas de festas com churrasqueira",
                        "garagem coberta":               "garagens cobertas",
                    }
                    plural = _PLURAIS.get(nome, nome + "s")
                    itens_comodos.append(f"{n} {plural}")
            except:
                itens_comodos.append(f"{qtd} {nome}")

    if itens_comodos:
        if partes:
            partes[-1] += f", é constituíd{'a' if c.get('material','').lower() == 'madeira' else 'o'} internamente de " + ", ".join(itens_comodos)
        else:
            partes.append("É constituída internamente de " + ", ".join(itens_comodos))

    # Externos
    if externos:
        partes.append(f"Possui {', '.join(externos)} na área externa")

    # Padrão construtivo e acabamentos
    pad_const = c.get("padrao_construtivo", "")
    pad_acab  = c.get("padrao_acabamento", "")
    if pad_const and pad_acab:
        partes.append(f"{pad_const} padrão construtivo e {pad_acab.lower()} padrão de acabamentos")
    elif pad_const:
        partes.append(f"{pad_const} padrão construtivo")

    # Montar primeiro bloco
    texto = ". ".join(p[0].upper() + p[1:] for p in partes if p) + ("." if partes else "")

    # Paredes internas
    paredes = c.get("paredes", [])
    paredes_livre = c.get("paredes_livre", "").strip()
    todos_paredes = list(paredes) + ([paredes_livre] if paredes_livre else [])
    if todos_paredes:
        texto += f" O acabamento das paredes internas é em {', '.join(todos_paredes)}."

    # Cobertura
    cobertura = c.get("cobertura", "")
    if cobertura:
        texto += f" A cobertura com {cobertura.lower()}."

    # Aberturas
    aberturas = c.get("aberturas", [])
    if aberturas:
        texto += f" As aberturas são em {' e '.join(aberturas) if len(aberturas) <= 2 else ', '.join(aberturas[:-1]) + ' e ' + aberturas[-1]}."

    # Grades
    if c.get("grades"):
        texto += " As janelas possuem grades de segurança em ferro."

    # Piso
    piso = c.get("piso", [])
    piso_livre = c.get("piso_livre", "").strip()
    todos_piso = list(piso) + ([piso_livre] if piso_livre else [])
    if todos_piso:
        texto += f" Os revestimentos do piso são em {' e '.join(todos_piso) if len(todos_piso) <= 2 else ', '.join(todos_piso[:-1]) + ' e ' + todos_piso[-1]}."

    # Teto
    teto = c.get("teto", [])
    teto_livre = c.get("teto_livre", "").strip()
    todos_teto = list(teto) + ([teto_livre] if teto_livre else [])
    if todos_teto:
        texto += f" O teto em {' e '.join(todos_teto) if len(todos_teto) <= 2 else ', '.join(todos_teto[:-1]) + ' e ' + todos_teto[-1]}."

    # Conservação da pintura
    cons_pintura = c.get("conservacao_pintura", "")
    if cons_pintura:
        texto += f" A pintura está em {cons_pintura.lower()} estado de conservação."

    # Conservação geral
    cons_geral = c.get("conservacao_geral", "")
    if cons_geral:
        texto += f" Edificação em {cons_geral.lower()} estado de conservação."

    # Observação fotos — texto varia conforme tipo de edificação
    if c.get("obs_fotos"):
        tipo = c.get("tipo_edificacao", "residencial")
        if tipo == "residencial":
            cond = "habitabilidade"
        elif tipo == "nao_residencial":
            cond = "uso"
        else:
            cond = "habitabilidade e uso"
        texto += f" Não foi possível tirar fotos internas, o que impossibilitou a verificação das condições de {cond}."

    # Habitabilidade / Uso
    tipo = c.get("tipo_edificacao", "residencial")
    if tipo in ("residencial", "misto"):
        hab = c.get("habitabilidade", "")
        if hab:
            texto += f" Condição de habitabilidade: {hab}."
            motivo_hab = c.get("habitabilidade_motivo", "").strip()
            if motivo_hab:
                texto += f" {motivo_hab}."
    if tipo in ("nao_residencial", "misto"):
        uso = c.get("condicao_uso", "")
        if uso:
            texto += f" Condição de uso: {uso}."
            motivo_uso = c.get("condicao_uso_motivo", "").strip()
            if motivo_uso:
                texto += f" {motivo_uso}."

    return texto.strip()


def _gerar_obs_privativo(c: dict) -> str:
    """Gera o texto obs para edificação no modelo de área privativa."""
    unidade   = c.get("unidade", "Apartamento")
    pavimento = c.get("pavimento", "").strip()
    edificio  = c.get("edificio", "").strip()
    areas     = c.get("areas_privativas", [])  # [{tipo, valor}]

    art, situ = _genero(unidade)

    # Parágrafo 1 — automático
    p1 = f"{unidade} {situ}"
    if pavimento:
        p1 += f" no {pavimento}º pavimento"
    if edificio:
        p1 += f" do {edificio}"
    if areas:
        areas_txt = ", ".join(f"{a['tipo']} de {a['valor']} m²" for a in areas[:-1])
        if len(areas) > 1:
            areas_txt += f" e {areas[-1]['tipo']} de {areas[-1]['valor']} m²"
        else:
            areas_txt = f"{areas[0]['tipo']} de {areas[0]['valor']} m²"
        p1 += f", o qual contém {areas_txt}"
    p1 += "."

    texto = p1

    # Paredes + teto + aberturas numa frase
    paredes   = c.get("paredes", [])
    paredes_livre = c.get("paredes_livre", "").strip()
    todos_paredes = list(paredes) + ([paredes_livre] if paredes_livre else [])
    teto      = c.get("teto", [])
    teto_livre = c.get("teto_livre", "").strip()
    todos_teto = list(teto) + ([teto_livre] if teto_livre else [])
    aberturas = c.get("aberturas", [])

    linha_acab = []
    if todos_paredes:
        linha_acab.append(f"As paredes possuem acabamento em {', '.join(todos_paredes)}")
    if todos_teto:
        linha_acab.append(f"o teto em {', '.join(todos_teto)}")
    if aberturas:
        ab_txt = ' e '.join(aberturas) if len(aberturas) <= 2 else ', '.join(aberturas[:-1]) + ' e ' + aberturas[-1]
        linha_acab.append(f"as aberturas são em {ab_txt}")
    if linha_acab:
        texto += " " + ", ".join(linha_acab) + "."

    # Piso
    piso = c.get("piso", [])
    piso_livre = c.get("piso_livre", "").strip()
    todos_piso = list(piso) + ([piso_livre] if piso_livre else [])
    if todos_piso:
        piso_txt = ' e '.join(todos_piso) if len(todos_piso) <= 2 else ', '.join(todos_piso[:-1]) + ' e ' + todos_piso[-1]
        texto += f" Possui piso em {piso_txt}."

    # Cômodos — texto livre ou grid
    comodos_texto = c.get("comodos_texto", "").strip()
    if comodos_texto:
        texto += f" O imóvel é composto por {comodos_texto}."
    else:
        # Usar grid de cômodos igual ao modelo padrão
        comodos = c.get("comodos", {})
        ordem_comodos = [
            "dormitório","suíte","BWC","lavabo","sala de estar","sala de jantar",
            "copa","cozinha","área de serviço","despensa","sacada","varanda",
            "área de festas com churrasqueira","garagem coberta"
        ]
        _PLURAIS = {
            "dormitório": "dormitórios", "suíte": "suítes", "BWC": "BWC's",
            "lavabo": "lavabos", "sala de estar": "salas de estar",
            "sala de jantar": "salas de jantar", "copa": "copas",
            "cozinha": "cozinhas", "área de serviço": "áreas de serviço",
            "despensa": "despensas", "sacada": "sacadas", "varanda": "varandas",
            "área de festas com churrasqueira": "áreas de festas com churrasqueira",
            "garagem coberta": "garagens cobertas",
        }
        itens = []
        for nome in ordem_comodos:
            qtd = comodos.get(nome)
            if not qtd: continue
            try:
                n = int(qtd)
                if n == 0: continue
                itens.append(f"1 {nome}" if n == 1 else f"{n} {_PLURAIS.get(nome, nome+'s')}")
            except:
                itens.append(f"{qtd} {nome}")
        if itens:
            texto += f" O imóvel é composto por {', '.join(itens)}."

    # Extras — frase livre
    extras_texto = c.get("extras_texto", "").strip()
    if extras_texto:
        texto += f" Possui {extras_texto}."

    # Conservação geral
    cons_geral = c.get("conservacao_geral", "")
    if cons_geral:
        texto += f" Em {cons_geral.lower()} estado de conservação geral."

    # Obs fotos
    if c.get("obs_fotos"):
        tipo = c.get("tipo_edificacao", "residencial")
        if tipo == "residencial":
            cond = "habitabilidade"
        elif tipo == "nao_residencial":
            cond = "uso"
        else:
            cond = "habitabilidade e uso"
        texto += f" Não foi possível tirar fotos internas, o que impossibilitou a verificação das condições de {cond}."

    # Habitabilidade / Uso
    tipo = c.get("tipo_edificacao", "residencial")
    if tipo in ("residencial", "misto"):
        hab = c.get("habitabilidade", "")
        if hab:
            texto += f" {hab}."
            motivo_hab = c.get("habitabilidade_motivo", "").strip()
            if motivo_hab:
                texto += f" {motivo_hab}."
    if tipo in ("nao_residencial", "misto"):
        uso = c.get("condicao_uso", "")
        if uso:
            texto += f" {uso}."
            motivo_uso = c.get("condicao_uso_motivo", "").strip()
            if motivo_uso:
                texto += f" {motivo_uso}."

    return texto.strip()
