// ── Estado global ────────────────────────────────────────────
const state = {
  config:      null,   // dados estáticos do Python
  dadosPDF:    null,   // dados extraídos pelo Gemini
  tipoLaudo:   'normal',
  sistema:     'multia',
  temCoords:   false,
  vagasCount:  0,
  divisoesAtivas: new Set(['Área Útil']),
  areaTotal:   '',
  confirmResolve: null,
  codigoNoMomento: null,   // código Infoel quando o PDF foi analisado
  pdfsAnalisados: 0,        // contador de PDFs analisados
  coop: 'outra',
  modoMultiplo: false,      // análise de várias matrículas juntas
  matriculasAtuais: [],     // [{numero, area}] — só preenchido no modo múltiplo
  modoArea: 'somar',        // 'somar' ou 'dividir' (divisões de área no modo múltiplo)
};

// ── Tema (claro/escuro — persiste entre sessões via localStorage) ──
const TEMA_STORAGE_KEY = 'multia_tema';
const ICON_SOL = '<svg class="btn-icon btn-icon--sm" viewBox="0 0 18 18" aria-hidden="true"><circle cx="9" cy="9" r="3"/><line x1="9" y1="1.5" x2="9" y2="3.2"/><line x1="9" y1="14.8" x2="9" y2="16.5"/><line x1="1.5" y1="9" x2="3.2" y2="9"/><line x1="14.8" y1="9" x2="16.5" y2="9"/><line x1="3.6" y1="3.6" x2="4.8" y2="4.8"/><line x1="13.2" y1="13.2" x2="14.4" y2="14.4"/><line x1="3.6" y1="14.4" x2="4.8" y2="13.2"/><line x1="13.2" y1="4.8" x2="14.4" y2="3.6"/></svg>';
const ICON_LUA = '<svg class="btn-icon btn-icon--sm" viewBox="0 0 18 18" aria-hidden="true"><path d="M14.5 10.5A6 6 0 1 1 7.5 3.5a5 5 0 0 0 7 7z"/></svg>';

function aplicarTema(claro) {
  document.body.classList.toggle('theme-light', claro);
  const btn = document.getElementById('btn-tema');
  btn.innerHTML = (claro ? ICON_LUA : ICON_SOL) +
    '<span class="btn-label">' + (claro ? 'Tema Escuro' : 'Tema Claro') + '</span>';
}

function toggleTema() {
  const claro = !document.body.classList.contains('theme-light');
  aplicarTema(claro);
  localStorage.setItem(TEMA_STORAGE_KEY, claro ? 'claro' : 'escuro');
}

// Aplica o tema salvo antes de qualquer outra coisa, pra não "piscar" escuro e depois claro
(function iniciarTema() {
  if (localStorage.getItem(TEMA_STORAGE_KEY) === 'claro') aplicarTema(true);
})();

// ── Inicialização ─────────────────────────────────────────────
window.addEventListener('pywebviewready', async () => {
  const cfg = await pywebview.api.get_config_inicial();
  state.config = cfg;
  buildForm(cfg);
  window._obsOpcionais = cfg.obs_opcionais || [];
  // Conectar automaticamente ao sistema padrão
  const r = await pywebview.api.conectar('multia');
  if (r.ok) setStatus('status-auth', `✅ Autenticado — ${r.nome}`, 'ok');

  // Carregar configuração de pasta base
  const cfgPasta = await pywebview.api.get_config();
  if (cfgPasta.pasta_base) {
    const nome = cfgPasta.pasta_base.split('\\').pop() || cfgPasta.pasta_base.split('/').pop();
    setStatus('status-pasta', `📁 ${nome}`, 'ok');
  }

  carregarChecklist();
});

// ── Log ───────────────────────────────────────────────────────
function addLog(msg) {
  const el = document.getElementById('log');
  el.textContent += msg + '\n';
  el.scrollTop = el.scrollHeight;
}

function limparLog() {
  document.getElementById('log').textContent = '';
}

// ── Eventos vindos do Python ──────────────────────────────────
function onEvent(event, data) {
  if (event === 'analise_concluida') {
    state.dadosPDF  = data.dados;
    state.temCoords = data.tem_coords;
    state.areaTotal = (data.dados || {}).area_total || '';
    setBtn('btn-kml',     data.tem_coords);
    setBtn('btn-parecer', true);
    setBtn('btn-capa',    true);
    setBtn('btn-sistemas', true);
    if (state.tipoLaudo === 'antigo') setBtn('btn-importar', true);
  }
  if (event === 'checklist_atualizado') {
    renderizarChecklist(data.itens || []);
  }
  if (event === 'analise_multipla_concluida') {
    state.dadosPDF        = data.dados;
    state.areaTotal        = data.area_total_soma || '';
    state.matriculasAtuais = data.matriculas || [];
  }
}

// ── Checklist da revisão ───────────────────────────────────────
async function carregarChecklist() {
  const r = await pywebview.api.get_checklist();
  renderizarChecklist((r && r.itens) || []);
}

function renderizarChecklist(itens) {
  const lista = document.getElementById('checklist-lista');
  if (!lista) return;
  lista.innerHTML = '';
  itens.forEach(item => {
    const concluido = !!item.feito || !!item.na;
    const row = document.createElement('div');
    row.style.cssText = `display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;` +
      `background:${concluido ? 'rgba(39,174,96,.16)' : 'var(--hover)'};` +
      `border:1px solid ${concluido ? 'rgba(39,174,96,.4)' : 'transparent'};transition:background .15s;`;

    if (item.na) {
      const badge = document.createElement('span');
      badge.textContent = '➖';
      badge.style.cssText = 'width:20px;text-align:center;flex-shrink:0;font-size:16px;';
      row.appendChild(badge);
    } else {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'checklist-checkbox';
      cb.checked = !!item.feito;
      cb.addEventListener('change', () => onToggleChecklist(item, cb));
      row.appendChild(cb);
    }

    const textoWrap = document.createElement('div');
    textoWrap.style.cssText = 'flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;';

    const label = document.createElement('span');
    label.textContent = item.label;
    label.style.cssText = `font-size:14px;font-weight:${concluido ? '700' : '500'};` +
      `color:${item.na ? 'var(--muted-dark)' : 'var(--hover-text)'};${item.na ? 'text-decoration:line-through;' : ''}`;
    textoWrap.appendChild(label);

    if (item.chave === 'edificacoes') {
      const link = document.createElement('a');
      link.href = 'javascript:void(0)';
      link.textContent = item.na ? '↺ desfazer' : 'marcar como não se aplica';
      link.style.cssText = 'font-size:11px;color:var(--muted-dark);text-decoration:underline;';
      link.onclick = () => onToggleNa(item.chave);
      textoWrap.appendChild(link);
    }

    row.appendChild(textoWrap);

    if (item.feito && item.origem === 'auto') {
      const selo = document.createElement('span');
      selo.textContent = '🤖';
      selo.title = 'Marcado automaticamente pelo MPA';
      selo.style.cssText = 'font-size:16px;flex-shrink:0;';
      row.appendChild(selo);
    }

    lista.appendChild(row);
  });
}

async function onToggleChecklist(item, cbEl) {
  if (item.feito) {
    // Desmarcar não precisa confirmar — não há risco de falso positivo.
    await pywebview.api.desmarcar_checklist(item.chave);
    carregarChecklist();
    return;
  }
  const confirmado = confirm(`O passo "${item.label}" não foi feito via MPA. Você fez manualmente?`);
  if (confirmado) {
    await pywebview.api.marcar_checklist_manual(item.chave);
    carregarChecklist();
  } else {
    cbEl.checked = false;
  }
}

async function onToggleNa(chave) {
  await pywebview.api.marcar_checklist_na(chave);
  carregarChecklist();
}

// ── Utilitários UI ────────────────────────────────────────────
function setStatus(id, msg, tipo='ok') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = msg;
  el.className = 'status-line' + (tipo === 'erro' ? ' erro' : '');
}

function setBtn(id, enabled, text=null) {
  const el = document.getElementById(id);
  if (!el) return;
  el.disabled = !enabled;
  if (text) {
    const label = el.querySelector('.btn-label');
    if (label) label.textContent = text;
    else el.textContent = text;
    el.classList.toggle('is-loading', !enabled);
  }
}

// ── Sistema ───────────────────────────────────────────────────
async function onSistema(val) {
  state.sistema = val;
  const r = await pywebview.api.conectar(val);
  if (r.ok) setStatus('status-auth', `✅ Autenticado — ${r.nome}`, 'ok');
  else      setStatus('status-auth', `❌ ${r.msg}`, 'erro');
}

// ── Buscar ────────────────────────────────────────────────────
async function selecionarPastaBase() {
  const r = await pywebview.api.selecionar_pasta_base();
  if (r.ok) {
    const nome = r.pasta.split('\\').pop() || r.pasta.split('/').pop();
    setStatus('status-pasta', `📁 ${nome}`, 'ok');
  }
}

async function buscar() {
  const busca = document.getElementById('input-busca').value.trim();
  if (!busca) return;
  setStatus('status-busca', '🔍 Buscando...', 'ok');
  const r = await pywebview.api.buscar_avaliacao(busca);
  if (r.ok) {
    setStatus('status-busca', `REG ${r.reg} — ${r.cidade}/${r.uf}`, 'ok');
    if (state.dadosPDF) setBtn('btn-capa', true);
    if (r.cidade && r.cidade !== '?' && r.uf && r.uf !== '?') setBtn('btn-sistemas', true);
  } else {
    setStatus('status-busca', `⚠️ ${r.msg}`, 'erro');
  }
}

// ── PDF ───────────────────────────────────────────────────────
async function selecionarPDF() {
  const r = await pywebview.api.selecionar_pdf();
  if (r.ok) carregarPDF(r.nome);
}

function onDrop(e) {
  e.preventDefault();
  document.getElementById('drop-zone').classList.remove('drag-over');
  const files = e.dataTransfer.files;
  if (!files.length) return;
  const path = files[0].path || files[0].name;
  if (path.toLowerCase().endsWith('.pdf')) {
    pywebview.api.definir_pdf(path).then(r => {
      if (r.ok) carregarPDF(r.nome);
    });
  } else {
    const dz = document.getElementById('drop-zone');
    dz.innerHTML = '⚠️ Apenas arquivos PDF';
    setTimeout(() => dz.innerHTML = '☁️&nbsp;&nbsp;Arraste o PDF aqui &nbsp;ou&nbsp; <strong>clique para selecionar</strong>', 2000);
  }
}

function carregarPDF(nome) {
  const dz = document.getElementById('drop-zone');
  dz.innerHTML = `📄 <strong>${nome}</strong>`;
  dz.classList.add('has-file');
}

async function analisarPDF() {
  if (state.modoMultiplo) {
    const r = await pywebview.api.analisar_multiplas_matriculas();
    if (!r.ok) alert(r.msg || 'Erro ao analisar.');
    return;
  }
  state.tipoLaudo = document.querySelector('[name=tipo-laudo]:checked').value;
  // Rastrear código no momento da análise
  state.codigoNoMomento = document.getElementById('input-busca').value.trim();
  setBtn('btn-analisar', false, 'Analisando...');
  await pywebview.api.analisar_pdf(state.tipoLaudo);
}

// ── Múltiplas matrículas ───────────────────────────────────────
function onToggleModoMultiplo(ativo) {
  state.modoMultiplo = ativo;
  document.getElementById('pdf-unico-wrap').style.display    = ativo ? 'none' : '';
  document.getElementById('pdf-multiplo-wrap').style.display = ativo ? '' : 'none';
  document.getElementById('radio-tipo-laudo').style.display  = ativo ? 'none' : '';
  pywebview.api.ativar_modo_multiplo(ativo);
  renderizarPdfsMultiplos([]);
}

function renderizarPdfsMultiplos(nomes) {
  const lista = document.getElementById('pdfs-multiplos-lista');
  lista.innerHTML = '';
  if (!nomes.length) {
    lista.innerHTML = '<span style="color:var(--muted-dark);font-size:12px;">Nenhum PDF adicionado.</span>';
    return;
  }
  nomes.forEach((nome, idx) => {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:6px;background:var(--hover);border-radius:5px;padding:4px 8px;';
    const label = document.createElement('span');
    label.textContent = `📄 ${nome}`;
    label.style.cssText = 'flex:1;font-size:12px;color:white;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
    const btnRemover = document.createElement('button');
    btnRemover.textContent = '✕';
    btnRemover.style.cssText = 'background:none;border:none;color:var(--red);cursor:pointer;font-weight:700;';
    btnRemover.onclick = () => removerPdfMultiplo(idx);
    row.appendChild(label);
    row.appendChild(btnRemover);
    lista.appendChild(row);
  });
}

async function adicionarPdfsMultiplos() {
  const r = await pywebview.api.adicionar_pdfs_multiplos();
  if (r.pdfs) renderizarPdfsMultiplos(r.pdfs);
  if (r.msg) addLog(`⚠️ ${r.msg}`);
}

async function removerPdfMultiplo(idx) {
  const r = await pywebview.api.remover_pdf_multiplo(idx);
  if (r.pdfs) renderizarPdfsMultiplos(r.pdfs);
}

// ── KML ───────────────────────────────────────────────────────
async function gerarKML() { await pywebview.api.gerar_kml(); }

// ── Capa ──────────────────────────────────────────────────────
async function preencherCapa() {
  // Validação de consistência código vs PDF
  const _codigoAgora = document.getElementById('input-busca').value.trim();
  if (state.dadosPDF) {
    if (_codigoAgora && state.codigoNoMomento && _codigoAgora !== state.codigoNoMomento) {
      const ok = await confirmar(
        "⚠️ Atenção",
        `Você está tentando preencher a capa do código ${_codigoAgora} com as informações da matrícula do código ${state.codigoNoMomento}. Deseja continuar?`
      );
      if (!ok) return;
    }
  }
  state.tipoLaudo = document.querySelector('[name=tipo-laudo]:checked').value;
  const r = await pywebview.api.preencher_capa(state.tipoLaudo);
  if (r.ok && r.pedir_obs) {
    // Laudo antigo — pede obs antes de prosseguir
    abrirObsAntigoParaCapa();
  }
}

function abrirObsAntigoParaCapa() {
  const lista = document.getElementById('obs-antigo-lista');
  if (!lista.hasChildNodes() && window._obsOpcionais) {
    window._obsOpcionais.forEach((txt, idx) => {
      const lbl = document.createElement('label');
      lbl.style.cssText = 'display:flex;gap:8px;align-items:flex-start;margin-bottom:6px;color:#1a2c5b;font-size:13px;';
      lbl.innerHTML = `<input type="checkbox" value="${idx}" style="margin-top:2px;"> ${txt}`;
      lista.appendChild(lbl);
    });
  }
  // Mudar o botão de confirmar para chamar preencherCapaComObs
  document.getElementById('obs-antigo-confirmar').onclick = confirmarObsAntigoParaCapa;
  document.getElementById('modal-obs-antigo').classList.add('open');
}

async function confirmarObsAntigoParaCapa() {
  const selecionadas = [...document.querySelectorAll('#obs-antigo-lista input:checked')].map(i => parseInt(i.value));
  const adicional    = document.getElementById('obs-antigo-adicional').value.trim();
  fecharObsAntigo();
  await pywebview.api.preencher_capa_com_obs(state.tipoLaudo, selecionadas, adicional);
}

// ── Vistoria ──────────────────────────────────────────────────
async function anexarCroquiArquivos() {
  const r = await pywebview.api.anexar_croqui_e_arquivos();
  if (!r.ok) addLog(`❌ ${r.msg}`);
}

async function validarVistoria() {
  const ok = await confirmar(
    'Confirmar Validação',
    'Marcará TODOS os campos da aba "Validar Vistoria" como "Não se aplica".\nCampos com valor serão registrados no log.\n\nContinuar?'
  );
  if (ok) await pywebview.api.validar_vistoria();
}

async function importarLaudo() {
  const r = await pywebview.api.importar_laudo();
  if (!r.ok) { addLog(`❌ ${r.msg}`); return; }
  const msg = `Grupos de vistoria: ${r.grupos_area} grupo(s) com área\nFotos encontradas: ${r.n_fotos} foto(s)\n\nQuer criar os grupos e adicionar as fotos?`;
  const ok  = await confirmar('Confirmar Importação', msg);
  if (ok) await pywebview.api.confirmar_importar();
}

function abrirObsAntigo() {
  const lista = document.getElementById('obs-antigo-lista');
  if (!lista.hasChildNodes() && window._obsOpcionais) {
    window._obsOpcionais.forEach((txt, idx) => {
      const lbl = document.createElement('label');
      lbl.style.cssText = 'display:flex;gap:8px;align-items:flex-start;margin-bottom:6px;color:#1a2c5b;font-size:13px;';
      lbl.innerHTML = `<input type="checkbox" value="${idx}" style="margin-top:2px;"> ${txt}`;
      lista.appendChild(lbl);
    });
  }
  // Botão padrão para importar laudo
  document.getElementById('obs-antigo-confirmar').onclick = confirmarObsAntigo;
  document.getElementById('modal-obs-antigo').classList.add('open');
}

function fecharObsAntigo() {
  document.getElementById('modal-obs-antigo').classList.remove('open');
}

async function confirmarObsAntigo() {
  const selecionadas = [...document.querySelectorAll('#obs-antigo-lista input:checked')].map(i => parseInt(i.value));
  const adicional    = document.getElementById('obs-antigo-adicional').value.trim();
  fecharObsAntigo();
  await pywebview.api.confirmar_importar_com_obs(selecionadas, adicional);
}

// ── Modal confirmação ─────────────────────────────────────────
function confirmar(titulo, msg) {
  return new Promise(resolve => {
    state.confirmResolve = resolve;
    document.getElementById('confirm-title').textContent = titulo;
    document.getElementById('confirm-msg').textContent   = msg;
    document.getElementById('confirm-modal').classList.add('open');
  });
}
function confirmRespond(val) {
  document.getElementById('confirm-modal').classList.remove('open');
  if (state.confirmResolve) { state.confirmResolve(val); state.confirmResolve = null; }
}

// ══════════════════════════════════════════════════════════════
// MODAL PARECER
// ══════════════════════════════════════════════════════════════

// ── Abrir / Fechar ────────────────────────────────────────────
async function abrirParecer() {
  const coop = state.coop || 'outra';

  // Buscar dados do PDF para pré-preencher
  const d = await pywebview.api.get_dados_parecer();
  // Mostrar/ocultar uso das terras conforme tipo do imóvel
  const isRural = (state.dadosPDF || {}).tipo_imovel === 'RURAL';
  mostrarUsoTerras(isRural);

  // Pré-preencher campos privativo
  document.getElementById('ap-num').value      = d.unidade      || '';
  document.getElementById('ap-edificio').value = d.complemento  || '';
  document.getElementById('coordenadas-manual').value = d.coordenadas_raw || '';

  // Fonte do croqui
  const selFonte = document.getElementById('fonte-croqui');
  if (d.fonte_croqui) {
    for (let opt of selFonte.options) {
      if (opt.value.toLowerCase().startsWith(d.fonte_croqui.toLowerCase().slice(0,30))) {
        selFonte.value = opt.value; break;
      }
    }
  }

  // Pré-selecionar posição se esquina
  if (d.posicao && d.posicao.toLowerCase().includes('esquina')) {
    const rb = document.querySelector('[name=posicao][value="Esquina de Quadra"]');
    if (rb) { rb.checked = true; rb.dispatchEvent(new Event('change')); }
  }

  // Modo múltiplas matrículas: escolha somar/dividir
  const modoAreaWrap = document.getElementById('modo-area-wrap');
  if (state.modoMultiplo && (state.matriculasAtuais || []).length > 1) {
    modoAreaWrap.style.display = '';
    document.querySelector('[name=modo-area][value="somar"]').checked = true;
    construirDivisoesPorMatricula();
    onModoArea('somar');
  } else {
    modoAreaWrap.style.display = 'none';
    onModoArea('somar');
  }

  document.getElementById('modal-parecer').classList.add('open');
}

function fecharParecer() {
  document.getElementById('modal-parecer').classList.remove('open');
}

// ── Coletar e salvar ──────────────────────────────────────────
async function salvarParecer() {
  state.tipoLaudo = document.querySelector('[name=tipo-laudo]:checked').value;
  const opcoes    = coletarOpcoes();
  fecharParecer();
  await pywebview.api.salvar_parecer(opcoes, state.tipoLaudo);
}

function coletarOpcoes() {
  const cfg = state.config;
  const e   = {};

  // Modelo
  e.modelo_parecer = document.querySelector('[name=modelo-parecer]:checked')?.value || 'padrao';

  // Cooperativa
  e.coop = document.querySelector('[name=coop]:checked')?.value || 'outra';
  pywebview.api.set_coop(e.coop);

  // Risco
  e.risco     = Array.from(document.querySelectorAll('.risco-resp:checked')).map(r => r.value);
  e.risco_obs = document.getElementById('risco-obs')?.value || 'Não aplicável.';

  // Opções do formulário (radio e checkbox)
  for (const op of cfg.opcoes) {
    if (op.tipo === 'radio') {
      const rb = document.querySelector(`[name=${op.id}]:checked`);
      e[op.id] = rb ? rb.value : (op.opcoes[0] || '');
    } else if (op.tipo === 'checkbox') {
      const checks = document.querySelectorAll(`[name=${op.id}]:checked`);
      e[op.id] = Array.from(checks).map(c => c.value).join(', ');
    } else if (op.tipo === 'texto') {
      const inp = document.getElementById(`field-${op.id}`);
      e[op.id] = inp ? inp.value.trim() : '';
    } else if (op.tipo === 'relevo_custom') {
      // Relevo montado pelo _on_relevo
      e.relevo_texto = document.getElementById('relevo-texto')?.value || '';
    }
  }

  // Inline extras
  e.rua_esquina  = document.getElementById('rua-esquina')?.value.trim()  || '';
  e.rodovia_nome = document.getElementById('rodovia-nome')?.value.trim() || '';
  e.acesso_checks= Array.from(document.querySelectorAll('[name=acesso-check]:checked')).map(c => c.value);
  e.acesso_livre = document.getElementById('acesso-livre')?.value.trim() || '';


  // Área privativa
  e.ap_num          = document.getElementById('ap-num')?.value.trim()          || '';
  e.ap_edificio     = document.getElementById('ap-edificio')?.value.trim()     || '';
  e.ap_empreendimento=document.getElementById('ap-empreendimento')?.value.trim()|| '';
  e.ap_construtora  = document.getElementById('ap-construtora')?.value.trim()  || '';
  e.ap_ano          = document.getElementById('ap-ano')?.value.trim()          || '';
  e.ap_dist_mar     = document.getElementById('ap-dist-mar')?.value.trim()     || '';
  e.ap_lazer        = Array.from(document.querySelectorAll('[name=lazer-item]:checked')).map(c => c.value);
  e.ap_lazer_livre  = document.getElementById('ap-lazer-livre')?.value.trim()  || '';
  e.dist_rodovia_ap = document.getElementById('ap-dist-rodovia')?.value.trim() || '';
  e.dist_centro_ap  = document.getElementById('ap-dist-centro')?.value.trim()  || '';
  // Vagas
  e.ap_vagas_qtd  = state.vagasCount;
  e.ap_vagas_nums = Array.from(document.querySelectorAll('.vaga-input')).map(i => i.value.trim());

  // Coordenadas e fonte
  e.coordenadas_manual = document.getElementById('coordenadas-manual')?.value.trim() || '';
  e.fonte_croqui       = document.getElementById('fonte-croqui')?.value || '';

  // Infraestrutura
  e.infra = Array.from(document.querySelectorAll('[name=infra-item]:checked')).map(c => c.value);

  // Divisões de área
  e.divisoes_area = {};
  for (const nome of state.divisoesAtivas) {
    const inp = document.getElementById(`div-val-${cssId(nome)}`);
    e.divisoes_area[nome] = inp ? inp.value.trim() : '';
  }

  // Modo múltiplas matrículas — somar (usa divisoes_area acima) ou dividir por matrícula
  e.modo_area = state.modoMultiplo ? state.modoArea : 'somar';
  if (e.modo_area === 'dividir') {
    e.divisoes_por_matricula = {};
    for (const numero of Object.keys(_blocosDivisoesPorMatricula)) {
      e.divisoes_por_matricula[numero] = _blocosDivisoesPorMatricula[numero].coletar();
    }
  }

  // Observações
  e.obs_extras   = Array.from(document.querySelectorAll('[name=obs-extra]:checked')).map(c => parseInt(c.value));
  e.obs_adicional= document.getElementById('obs-adicional')?.value.trim() || '';
  e.uso_terras   = Array.from(document.querySelectorAll('[name=uso-terras-item]:checked')).map(c => c.value);

  // Análise jurídica (itens marcados pelo usuário)
  e.analise_juridica_selecionada = Array.from(document.querySelectorAll('[id^="av-jur-"]:checked'))
    .map(cb => {
      const idx = parseInt(cb.dataset.idx);
      return (window._analiseJuridica || [])[idx] || null;
    })
    .filter(Boolean);

  return e;
}

// ── Construção dinâmica do formulário ─────────────────────────
function buildForm(cfg) {
  buildRiscoPerguntas(cfg.risco_perguntas);
  buildFonteCroqui(cfg.opcoes_fonte_croqui);
  buildInfra(cfg.infra_itens);
  buildObs(cfg.obs_opcionais);
  buildDivisoes();
  buildLazer();
  buildUsoTerras();

  const ESQUERDA = ['posicao','relevo','vegetacao','veg_extra','formato','solo','cercamento','calcada','meio_fio'];
  const DIREITA  = ['extras','tipo_via','pavimentacao','conservacao','tipo_rua','padrao','dist_rodovia','dist_centro'];

  const colE = document.getElementById('col-esquerda');
  const colD = document.getElementById('col-direita');

  for (const op of cfg.opcoes) {
    if (ESQUERDA.includes(op.id))      renderOp(colE, op);
    else if (DIREITA.includes(op.id))  renderOp(colD, op);
  }
}

function renderOp(parent, op) {
  const sec = document.createElement('div');
  sec.className = 'form-section';

  if (op.id !== 'dist_centro') {
    const t = document.createElement('div');
    t.className = 'form-section-title';
    t.textContent = op.label;
    sec.appendChild(t);
  }

  if (op.tipo === 'relevo_custom') {
    sec.appendChild(buildRelevo());
  } else if (op.tipo === 'radio') {
    const grp = document.createElement('div');
    grp.className = 'form-radio-group';
    for (const opc of op.opcoes) {
      const lbl = document.createElement('label');
      const rb  = document.createElement('input');
      rb.type = 'radio'; rb.name = op.id; rb.value = opc;
      if (opc === op.opcoes[0]) rb.checked = true;

      lbl.appendChild(rb);
      lbl.appendChild(document.createTextNode(' ' + opc));
      grp.appendChild(lbl);

      // Inline extras
      if (op.id === 'posicao' && opc === 'Esquina de Quadra') {
        const extra = buildInlineExtra('rua-esquina', 'Rua da esquina:', 'ex: Rua das Flores');
        grp.appendChild(extra);
        rb.addEventListener('change', () => {
          document.querySelectorAll('[name=posicao]').forEach(r => {
            const ex = r.parentElement.nextElementSibling;
            if (ex && ex.id === 'extra-rua-esquina') ex.classList.toggle('visible', r.checked && r.value === 'Esquina de Quadra');
          });
          checkPosicaoEsquina();
        });
      }
      if (op.id === 'tipo_via' && opc === 'Rodovia (Às Margens)') {
        const extra = buildInlineExtra('rodovia-nome', 'Nome da rodovia:', 'ex: BR-116');
        grp.appendChild(extra);
        rb.addEventListener('change', () => onTipoVia());
      }
      if (op.id === 'tipo_via') {
        rb.addEventListener('change', () => onTipoVia());
      }
    }
    sec.appendChild(grp);

    // Acesso checks e livre (abaixo do tipo_via)
    if (op.id === 'tipo_via') {
      sec.appendChild(buildAcessoExtras());
    }

  } else if (op.tipo === 'checkbox') {
    const grp = document.createElement('div');
    grp.className = 'form-check-group';
    for (const opc of op.opcoes) {
      const lbl = document.createElement('label');
      const cb  = document.createElement('input');
      cb.type = 'checkbox'; cb.name = op.id; cb.value = opc;
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(' ' + opc));
      grp.appendChild(lbl);
    }
    sec.appendChild(grp);

  } else if (op.tipo === 'texto') {
    const wrap = document.createElement('div');
    if (op.id === 'dist_centro') {
      // Inline com dist_rodovia — será tratado junto
      const t = document.createElement('div');
      t.className = 'form-section-title'; t.textContent = op.label;
      wrap.appendChild(t);
    }
    const inp = document.createElement('input');
    inp.type = 'text'; inp.id = `field-${op.id}`;
    inp.placeholder = op.placeholder || '';
    inp.style.width = '160px';
    wrap.appendChild(inp);


    sec.appendChild(wrap);
  }

  parent.appendChild(sec);
}

// ── Relevo custom ─────────────────────────────────────────────
function buildRelevo() {
  const wrap = document.createElement('div');

  // Hidden input para o texto final
  const hidden = document.createElement('input');
  hidden.type = 'hidden'; hidden.id = 'relevo-texto'; hidden.value = 'totalmente em aclive';
  wrap.appendChild(hidden);

  const MODS = ['Totalmente','Parcialmente','Predominantemente'];
  const DIRS = ['Em Aclive','Plano','Em Declive'];

  // Nível 1 — modificador
  const grpMod = document.createElement('div');
  grpMod.className = 'form-radio-group';
  for (const m of MODS) {
    const lbl = document.createElement('label');
    const rb  = document.createElement('input');
    rb.type = 'radio'; rb.name = 'relevo-mod'; rb.value = m;
    if (m === 'Totalmente') rb.checked = true;
    rb.addEventListener('change', atualizarRelevo);
    lbl.appendChild(rb); lbl.appendChild(document.createTextNode(' ' + m));
    grpMod.appendChild(lbl);
  }
  wrap.appendChild(grpMod);

  // Nível 2 — direção principal
  const lblTerreno = document.createElement('label');
  lblTerreno.style.cssText = 'font-size:12px;color:var(--text-muted);display:block;margin:4px 0 2px;';
  lblTerreno.textContent = 'Terreno:';
  wrap.appendChild(lblTerreno);

  const grpDir = document.createElement('div');
  grpDir.className = 'sub-radio visible';
  grpDir.id = 'relevo-dir-group';
  for (const d of DIRS) {
    const lbl = document.createElement('label');
    const rb  = document.createElement('input');
    rb.type = 'radio'; rb.name = 'relevo-dir'; rb.value = d;
    if (d === 'Em Aclive') rb.checked = true;
    rb.addEventListener('change', atualizarRelevo);
    lbl.appendChild(rb); lbl.appendChild(document.createTextNode(' ' + d));
    grpDir.appendChild(lbl);
  }
  wrap.appendChild(grpDir);

  // Nível 3 — complemento (oculto quando Totalmente)
  const lblComp = document.createElement('label');
  lblComp.style.cssText = 'font-size:12px;color:var(--text-muted);display:block;margin:4px 0 2px;';
  lblComp.textContent = 'Com áreas:';
  lblComp.id = 'relevo-comp-label';
  wrap.appendChild(lblComp);

  const grpComp = document.createElement('div');
  grpComp.className = 'sub-radio';
  grpComp.id = 'relevo-comp-group';
  for (const d of DIRS) {
    const lbl = document.createElement('label');
    const rb  = document.createElement('input');
    rb.type = 'radio'; rb.name = 'relevo-comp'; rb.value = d;
    if (d === 'Em Aclive') rb.checked = true;
    rb.addEventListener('change', atualizarRelevo);
    lbl.appendChild(rb); lbl.appendChild(document.createTextNode(' ' + d));
    grpComp.appendChild(lbl);
  }
  wrap.appendChild(grpComp);

  // Estado inicial
  setTimeout(atualizarRelevo, 0);
  return wrap;
}

function atualizarRelevo() {
  const mod  = document.querySelector('[name=relevo-mod]:checked')?.value  || 'Totalmente';
  const dir  = document.querySelector('[name=relevo-dir]:checked')?.value  || 'Em Aclive';
  const comp = document.querySelector('[name=relevo-comp]:checked')?.value || 'Em Aclive';

  const isTotalmente = mod === 'Totalmente';
  const compGroup    = document.getElementById('relevo-comp-group');
  const compLabel    = document.getElementById('relevo-comp-label');
  if (compGroup) compGroup.classList.toggle('visible', !isTotalmente);
  if (compLabel) compLabel.style.display = isTotalmente ? 'none' : 'block';

  // Complemento precisa concordar: "Plano" → "planas"
  function _concordarComp(val) {
    if (val === 'Plano') return 'planas';
    return val.toLowerCase();
  }

  let texto;
  if (isTotalmente) {
    texto = `totalmente ${dir.toLowerCase()}`;
  } else {
    texto = `${mod.toLowerCase()} ${dir.toLowerCase()}, com áreas ${_concordarComp(comp)}`;
  }
  const hidden = document.getElementById('relevo-texto');
  if (hidden) hidden.value = texto;
}

// ── Inline extras ─────────────────────────────────────────────
function buildInlineExtra(inputId, labelText, placeholder) {
  const div = document.createElement('div');
  div.className = 'inline-extra';
  div.id = `extra-${inputId}`;
  const lbl = document.createElement('label');
  lbl.textContent = labelText;
  const inp = document.createElement('input');
  inp.type = 'text'; inp.id = inputId; inp.placeholder = placeholder;
  div.appendChild(lbl); div.appendChild(inp);
  return div;
}

function checkPosicaoEsquina() {
  const esquina = document.querySelector('[name=posicao][value="Esquina de Quadra"]')?.checked;
  const extra   = document.getElementById('extra-rua-esquina');
  if (extra) extra.classList.toggle('visible', !!esquina);
}

function onTipoVia() {
  const val    = document.querySelector('[name=tipo_via]:checked')?.value || '';
  const extra  = document.getElementById('extra-rodovia-nome');
  const distEl = document.getElementById('field-dist_rodovia')?.closest('.form-section');
  const margem = val === 'Rodovia (Às Margens)';
  if (extra)  extra.classList.toggle('visible', margem);
  if (distEl) distEl.style.display = margem ? 'none' : '';
}

function buildAcessoExtras() {
  const wrap = document.createElement('div');
  wrap.style.marginTop = '6px';

  const CHECKS = [
    'Passa por ponte de madeira',
    'Passa por ponte de concreto',
    'Acesso compartilhado com outros imóveis',
  ];
  const grp = document.createElement('div');
  grp.className = 'form-check-group';
  for (const c of CHECKS) {
    const lbl = document.createElement('label');
    const cb  = document.createElement('input');
    cb.type = 'checkbox'; cb.name = 'acesso-check'; cb.value = c;
    lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + c));
    grp.appendChild(lbl);
  }
  wrap.appendChild(grp);

  const livreLbl = document.createElement('label');
  livreLbl.style.cssText = 'font-size:12px;color:var(--text-muted);display:block;margin-top:6px;';
  livreLbl.textContent = 'Descrição adicional do acesso (opcional):';
  const livreInp = document.createElement('input');
  livreInp.type = 'text'; livreInp.id = 'acesso-livre';
  livreInp.placeholder = 'ex: com trecho não pavimentado';
  livreInp.style.width = '100%';
  wrap.appendChild(livreLbl); wrap.appendChild(livreInp);
  return wrap;
}

// ── Risco ─────────────────────────────────────────────────────
function buildRiscoPerguntas(perguntas) {
  const cont = document.getElementById('risco-perguntas');
  perguntas.forEach((q, i) => {
    const div = document.createElement('div');
    div.className = 'risco-pergunta';
    const p = document.createElement('p'); p.textContent = q;
    const opts = document.createElement('div'); opts.className = 'radio-opts';
    for (const opt of ['Não','Sim','Inconclusivo']) {
      const lbl = document.createElement('label');
      const rb  = document.createElement('input');
      rb.type = 'radio'; rb.name = `risco-${i}`; rb.value = opt;
      rb.className = 'risco-resp';
      if (opt === 'Não') rb.checked = true;
      lbl.appendChild(rb); lbl.appendChild(document.createTextNode(' ' + opt));
      opts.appendChild(lbl);
    }
    div.appendChild(p); div.appendChild(opts);
    cont.appendChild(div);
  });
}

function onCoop(val) {
  state.coop = val;
  const box = document.getElementById('risco-box');
  // Risco socioambiental apenas para Sicoob Maxicrédito e Sicredi Biomas
  const temRisco = val === 'maxicredito' || val === 'biomas';
  box.classList.toggle('visible', temRisco);

  // Análise jurídica — carregar já ao selecionar Maxicrédito
  const ajBox = document.getElementById('av-juridica-box');
  if (!ajBox) return;
  if (val === 'maxicredito') {
    pywebview.api.get_analise_juridica().then(aj => {
      window._analiseJuridica = aj.itens || [];
      if (window._analiseJuridica.length > 0) {
        ajBox.style.display = 'block';
        const lista = document.getElementById('av-juridica-lista');
        lista.innerHTML = '';
        window._analiseJuridica.forEach((item, idx) => {
          const icone = item.impacto === 'Alto' ? '🔴' : item.impacto === 'Médio' ? '🟡' : '🟢';
          const div   = document.createElement('div');
          div.style.cssText = 'display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;';
          div.innerHTML = `
            <input type="checkbox" id="av-jur-${idx}" data-idx="${idx}" style="margin-top:3px;">
            <label for="av-jur-${idx}" style="font-size:13px;cursor:pointer;">
              ${icone} <strong>${item.id_av}</strong> — ${item.subtipo}: ${item.descricao}
              ${item.credora ? `<em style="color:var(--text-muted)"> | ${item.credora}</em>` : ''}
            </label>`;
          lista.appendChild(div);
        });
      } else {
        ajBox.style.display = 'none';
      }
    });
  } else {
    window._analiseJuridica = [];
    ajBox.style.display = 'none';
  }
}

// ── Modelo parecer ────────────────────────────────────────────
function onModeloParecer(val) {
  document.getElementById('privativo-box').classList.toggle('visible', val === 'privativo');
  document.getElementById('form-cols').style.display      = val === 'privativo' ? 'none' : '';
}

// ── Fonte do croqui ───────────────────────────────────────────
// Opções da fonte do croqui (salvas para filtro)
let _fonteCroquiOpcoes = [];

function buildFonteCroqui(opcoes) {
  _fonteCroquiOpcoes = opcoes;
  const inp = document.getElementById('fonte-croqui');
  if (inp && opcoes.length) inp.value = opcoes[0];
  _renderFonteCroquiOpcoes(opcoes);
}

function _renderFonteCroquiOpcoes(opcoes) {
  const dd = document.getElementById('fonte-croqui-dropdown');
  if (!dd) return;
  dd.innerHTML = '';
  const atual = document.getElementById('fonte-croqui')?.value || '';
  for (const o of opcoes) {
    const div = document.createElement('div');
    div.className = 'custom-select-option' + (o === atual ? ' selected' : '');
    div.textContent = o;
    div.addEventListener('mousedown', (e) => {
      e.preventDefault();
      document.getElementById('fonte-croqui').value = o;
      fecharFonteCroqui();
      _renderFonteCroquiOpcoes(_fonteCroquiOpcoes);
    });
    dd.appendChild(div);
  }
}

function abrirFonteCroqui() {
  const dd = document.getElementById('fonte-croqui-dropdown');
  if (dd) dd.style.display = 'block';
  _renderFonteCroquiOpcoes(_fonteCroquiOpcoes);
}

function fecharFonteCroqui() {
  const dd = document.getElementById('fonte-croqui-dropdown');
  if (dd) dd.style.display = 'none';
}

function toggleFonteCroqui() {
  const dd = document.getElementById('fonte-croqui-dropdown');
  if (!dd) return;
  if (dd.style.display === 'none') { abrirFonteCroqui(); }
  else { fecharFonteCroqui(); }
}

function filtrarFonteCroqui(termo) {
  const filtradas = _fonteCroquiOpcoes.filter(o =>
    o.toLowerCase().includes(termo.toLowerCase())
  );
  _renderFonteCroquiOpcoes(filtradas.length ? filtradas : _fonteCroquiOpcoes);
  abrirFonteCroqui();
}

// ── Infraestrutura ────────────────────────────────────────────
function buildInfra(itens) {
  const cont = document.getElementById('infra-list');
  for (const item of itens) {
    const lbl = document.createElement('label');
    const cb  = document.createElement('input');
    cb.type = 'checkbox'; cb.name = 'infra-item'; cb.value = item; cb.checked = true;
    lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + item));
    cont.appendChild(lbl);
  }
}

// ── Observações ───────────────────────────────────────────────
function buildObs(opcionais) {
  const cont = document.getElementById('obs-list');
  opcionais.forEach((obs, i) => {
    const lbl = document.createElement('label');
    const cb  = document.createElement('input');
    cb.type = 'checkbox'; cb.name = 'obs-extra'; cb.value = i;
    lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + obs));
    cont.appendChild(lbl);
  });
}

// ── Divisões de área ──────────────────────────────────────────
const DIVISOES_AREA = [
  'Área Útil','Área de Preservação Permanente','Área de Reserva Legal',
  'Área de Mata Nativa','Área Mecanizada','Área Mecanizável','Área de Reflorestamento',
  'Área não edificável',
];

const USO_TERRAS_OPCOES = [
  'Pastagem',
  'Lavoura',
  'Dupla Aptidão (Pastagem e lavoura)',
  'Agroindustrial',
  'Mineração',
  'Fruticultura ou Parreiral',
  'Granjas suínas ou aviários',
];

function buildDivisoes() {
  const checks  = document.getElementById('divisoes-checks');
  const entradas= document.getElementById('divisoes-entradas');

  for (const nome of DIVISOES_AREA) {
    // Checkbox
    const lbl = document.createElement('label');
    const cb  = document.createElement('input');
    cb.type = 'checkbox'; cb.value = nome;
    if (nome === 'Área Útil') { cb.checked = true; }
    cb.addEventListener('change', () => atualizarDivisoes());
    lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + nome));
    checks.appendChild(lbl);

    // Entrada
    const entry = document.createElement('div');
    entry.className = 'divisao-entry';
    entry.id = `div-entry-${cssId(nome)}`;
    entry.style.display = 'none';
    const entLbl = document.createElement('label'); entLbl.textContent = nome + ':';
    const entInp = document.createElement('input');
    entInp.type = 'text'; entInp.id = `div-val-${cssId(nome)}`;
    entInp.placeholder = 'ex: 1,3000';
    entInp.addEventListener('blur', recalcularDivisoes);
    entry.appendChild(entLbl); entry.appendChild(entInp);
    entradas.appendChild(entry);
  }

  atualizarDivisoes();
}

function atualizarDivisoes() {
  const ativas = Array.from(document.querySelectorAll('#divisoes-checks input:checked')).map(c => c.value);
  state.divisoesAtivas = new Set(ativas);
  const entradas = document.getElementById('divisoes-entradas');
  const mostra   = ativas.length > 1;
  entradas.classList.toggle('visible', mostra);
  for (const nome of DIVISOES_AREA) {
    const entry = document.getElementById(`div-entry-${cssId(nome)}`);
    if (entry) entry.style.display = (mostra && ativas.includes(nome)) ? '' : 'none';
  }
  if (mostra) recalcularDivisoes();
}

function recalcularDivisoes() {
  const ativas = Array.from(state.divisoesAtivas);
  if (ativas.length <= 1) return;
  const total = parseFloat((state.areaTotal || '0').replace(/[^\d,.]/g,'').replace(',','.')) || 0;
  if (!total) return;
  const tof = v => { const n = (v||'').replace(',','.').replace(/[^\d.]/g,''); return parseFloat(n) || null; };
  const vazios      = ativas.filter(n => tof(document.getElementById(`div-val-${cssId(n)}`)?.value) === null);
  const preenchidos = ativas.filter(n => tof(document.getElementById(`div-val-${cssId(n)}`)?.value) !== null);
  if (vazios.length === 1) {
    const soma     = preenchidos.reduce((s,n) => s + (tof(document.getElementById(`div-val-${cssId(n)}`).value)||0), 0);
    const restante = total - soma;
    if (restante >= 0) {
      const inp = document.getElementById(`div-val-${cssId(vazios[0])}`);
      if (inp) inp.value = restante.toFixed(4).replace('.',',');
    }
  }
}

// ── Divisões de área — modo múltiplas matrículas (dividir por matrícula) ──
function onModoArea(valor) {
  state.modoArea = valor;
  document.getElementById('divisoes-somar-wrap').style.display = valor === 'somar' ? '' : 'none';
  document.getElementById('divisoes-por-matricula-wrap').style.display = valor === 'dividir' ? '' : 'none';
}

let _blocosDivisoesPorMatricula = {};

function construirDivisoesPorMatricula() {
  const wrap = document.getElementById('divisoes-por-matricula-wrap');
  wrap.innerHTML = '';
  _blocosDivisoesPorMatricula = {};

  for (const mat of (state.matriculasAtuais || [])) {
    const numero  = mat.numero;
    const idSuf   = cssId(String(numero));
    const areaNum = parseFloat(String(mat.area || '0').replace(/[^\d,.]/g,'').replace(',','.')) || 0;
    const ativas  = new Set(['Área Útil']);

    const bloco = document.createElement('div');
    bloco.style.cssText = 'margin-bottom:14px;padding:10px;border:1px solid var(--border);border-radius:6px;';
    const titulo = document.createElement('div');
    titulo.textContent = `Matrícula ${numero} — área ${mat.area || '?'}`;
    titulo.style.cssText = 'font-weight:700;font-size:12px;color:var(--primary);margin-bottom:6px;';
    bloco.appendChild(titulo);

    const checks   = document.createElement('div');
    checks.className = 'divisoes-check-list';
    const entradas = document.createElement('div');
    entradas.className = 'divisoes-entradas';

    function atualizar() {
      const marcadas = Array.from(checks.querySelectorAll('input:checked')).map(c => c.value);
      ativas.clear(); marcadas.forEach(v => ativas.add(v));
      const mostra = marcadas.length > 1;
      entradas.classList.toggle('visible', mostra);
      for (const nome of DIVISOES_AREA) {
        const entry = document.getElementById(`div-entry-${idSuf}-${cssId(nome)}`);
        if (entry) entry.style.display = (mostra && marcadas.includes(nome)) ? '' : 'none';
      }
      if (mostra) recalcular();
    }

    function recalcular() {
      const lista = Array.from(ativas);
      if (lista.length <= 1 || !areaNum) return;
      const tof = v => { const n = (v||'').replace(',','.').replace(/[^\d.]/g,''); return parseFloat(n) || null; };
      const vazios      = lista.filter(n => tof(document.getElementById(`div-val-${idSuf}-${cssId(n)}`)?.value) === null);
      const preenchidos = lista.filter(n => tof(document.getElementById(`div-val-${idSuf}-${cssId(n)}`)?.value) !== null);
      if (vazios.length === 1) {
        const soma     = preenchidos.reduce((s,n) => s + (tof(document.getElementById(`div-val-${idSuf}-${cssId(n)}`).value)||0), 0);
        const restante = areaNum - soma;
        if (restante >= 0) {
          const inp = document.getElementById(`div-val-${idSuf}-${cssId(vazios[0])}`);
          if (inp) inp.value = restante.toFixed(4).replace('.',',');
        }
      }
    }

    for (const nome of DIVISOES_AREA) {
      const lbl = document.createElement('label');
      const cb  = document.createElement('input');
      cb.type = 'checkbox'; cb.value = nome;
      if (nome === 'Área Útil') cb.checked = true;
      cb.addEventListener('change', atualizar);
      lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + nome));
      checks.appendChild(lbl);

      const entry = document.createElement('div');
      entry.className = 'divisao-entry';
      entry.id = `div-entry-${idSuf}-${cssId(nome)}`;
      entry.style.display = 'none';
      const entLbl = document.createElement('label'); entLbl.textContent = nome + ':';
      const entInp = document.createElement('input');
      entInp.type = 'text'; entInp.id = `div-val-${idSuf}-${cssId(nome)}`;
      entInp.placeholder = 'ex: 1,3000';
      entInp.addEventListener('blur', recalcular);
      entry.appendChild(entLbl); entry.appendChild(entInp);
      entradas.appendChild(entry);
    }

    bloco.appendChild(checks);
    bloco.appendChild(entradas);
    wrap.appendChild(bloco);
    atualizar();

    _blocosDivisoesPorMatricula[numero] = {
      coletar: () => {
        const out = {};
        for (const nome of ativas) {
          const inp = document.getElementById(`div-val-${idSuf}-${cssId(nome)}`);
          out[nome] = inp ? inp.value.trim() : '';
        }
        return out;
      },
    };
  }
}

// ── Lazer ─────────────────────────────────────────────────────
const LAZER_ITENS = [
  'piscina adulto e infantil','salão de festas','sala de jogos','playground',
  'hidromassagem','hall de entrada decorado','acesso controlado por biometria e tag',
  'box de praia privativo','elevador',
];
function buildLazer() {
  const grid = document.getElementById('lazer-grid');
  for (const item of LAZER_ITENS) {
    const lbl = document.createElement('label');
    const cb  = document.createElement('input');
    cb.type = 'checkbox'; cb.name = 'lazer-item'; cb.value = item;
    lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + item));
    grid.appendChild(lbl);
  }
}

// ── Uso das Terras ───────────────────────────────────────────
function buildUsoTerras() {
  const cont = document.getElementById('uso-terras-checks');
  if (!cont || cont.children.length) return;
  for (const item of USO_TERRAS_OPCOES) {
    const lbl = document.createElement('label');
    const cb  = document.createElement('input');
    cb.type = 'checkbox'; cb.name = 'uso-terras-item'; cb.value = item;
    lbl.appendChild(cb); lbl.appendChild(document.createTextNode(' ' + item));
    cont.appendChild(lbl);
  }
}

function mostrarUsoTerras(isRural) {
  const sec = document.getElementById('uso-terras-section');
  if (sec) sec.style.display = isRural ? '' : 'none';
}

// ── Vagas ─────────────────────────────────────────────────────
function ajustarVagas(delta) {
  state.vagasCount = Math.max(0, state.vagasCount + delta);
  document.getElementById('vagas-count').textContent = state.vagasCount;
  const cont = document.getElementById('vagas-entradas');
  cont.innerHTML = '';
  for (let i = 0; i < state.vagasCount; i++) {
    const div = document.createElement('div'); div.className = 'vaga-entry';
    const lbl = document.createElement('label'); lbl.textContent = `Vaga ${i+1} nº:`;
    const inp = document.createElement('input');
    inp.type = 'text'; inp.className = 'vaga-input'; inp.placeholder = 'ex: 12';
    div.appendChild(lbl); div.appendChild(inp);
    cont.appendChild(div);
  }
}

// ── Utilitário ────────────────────────────────────────────────
function cssId(str) {
  return str.toLowerCase().replace(/[^a-z0-9]/g, '-');
}


// ══════════════════════════════════════════════════════════════
// EDIFICAÇÕES
// ══════════════════════════════════════════════════════════════

const edState = {
  lista:       [],   // edificações adicionadas
  areasCount:  0,    // contador de áreas privativas
};

const COMODOS_LISTA = [
  'dormitório','suíte','BWC','lavabo','sala de estar','sala de jantar',
  'copa','cozinha','área de serviço','despensa','sacada','varanda',
  'área de festas com churrasqueira','garagem coberta',
];

// ── Abrir / Fechar ────────────────────────────────────────────
async function abrirEdificacoes() {
  // Pré-preencher dados do Gemini se disponíveis
  if (state.dadosPDF) {
    const d = await pywebview.api.get_dados_edificacao();
    if (d.unidade)    document.getElementById('ed-nome').value     = d.unidade;
    if (d.complemento)document.getElementById('ed-edificio').value = d.complemento;
    if (d.pavimento)  document.getElementById('ed-pavimento').value= d.pavimento;
    // Áreas privativas
    if (d.areas_privativas && d.areas_privativas.length) {
      document.getElementById('ed-areas-list').innerHTML = '';
      edState.areasCount = 0;
      for (const a of d.areas_privativas) addAreaPrivativa(a.tipo, a.valor);
    }
  }

  buildComodosGrid();
  buildComodosPrivativoGrid();
  atualizarListaEd();
  document.getElementById('modal-edificacoes').classList.add('open');
}

function fecharEdificacoes() {
  document.getElementById('modal-edificacoes').classList.remove('open');
}

// ── Cômodos grid ──────────────────────────────────────────────
function buildComodosGrid() {
  const grid = document.getElementById('ed-comodos-grid');
  if (grid.children.length) return; // já construído
  for (const nome of COMODOS_LISTA) {
    const field = document.createElement('div');
    field.className = 'field-row';
    const lbl = document.createElement('label');
    lbl.textContent = nome.charAt(0).toUpperCase() + nome.slice(1) + ':';
    const inp = document.createElement('input');
    inp.type = 'number'; inp.min = '0'; inp.id = `ed-comodo-${cssId(nome)}`;
    inp.placeholder = '0'; inp.style.width = '100%';
    field.appendChild(lbl); field.appendChild(inp);
    grid.appendChild(field);
  }
}

// ── Cômodos grid privativo ───────────────────────────────────
function buildComodosPrivativoGrid() {
  const grid = document.getElementById('ed-privativo-comodos-grid');
  if (!grid || grid.children.length) return;
  for (const nome of COMODOS_LISTA) {
    const field = document.createElement('div');
    field.className = 'field-row';
    const lbl = document.createElement('label');
    lbl.textContent = nome.charAt(0).toUpperCase() + nome.slice(1) + ':';
    const inp = document.createElement('input');
    inp.type = 'number'; inp.min = '0'; inp.id = `ed-priv-comodo-${cssId(nome)}`;
    inp.placeholder = '0'; inp.style.width = '100%';
    field.appendChild(lbl); field.appendChild(inp);
    grid.appendChild(field);
  }
}

// ── Áreas privativas dinâmicas ────────────────────────────────
const TIPOS_AREA = ['área privativa','área de uso comum','área total','fração ideal','área útil'];

function addAreaPrivativa(tipoVal='', valorVal='') {
  edState.areasCount++;
  const id = edState.areasCount;
  const row = document.createElement('div');
  row.className = 'area-privativa-row';
  row.id = `area-row-${id}`;

  const sel = document.createElement('select');
  sel.style.width = '200px';
  for (const t of TIPOS_AREA) {
    const opt = document.createElement('option');
    opt.value = t; opt.textContent = t;
    sel.appendChild(opt);
  }
  // Opção personalizada
  const optCustom = document.createElement('option');
  optCustom.value = '__custom__'; optCustom.textContent = 'Outro (digitar)';
  sel.appendChild(optCustom);
  if (tipoVal) sel.value = TIPOS_AREA.includes(tipoVal) ? tipoVal : '__custom__';

  const inpTipo = document.createElement('input');
  inpTipo.type = 'text'; inpTipo.placeholder = 'Tipo da área';
  inpTipo.style.display = sel.value === '__custom__' ? '' : 'none';
  inpTipo.style.flex = '1';
  if (tipoVal && !TIPOS_AREA.includes(tipoVal)) inpTipo.value = tipoVal;

  sel.addEventListener('change', () => {
    inpTipo.style.display = sel.value === '__custom__' ? '' : 'none';
  });

  const inpValor = document.createElement('input');
  inpValor.type = 'text'; inpValor.placeholder = 'ex: 73,76';
  inpValor.style.flex = '1';
  if (valorVal) inpValor.value = valorVal;

  const lblM2 = document.createElement('span');
  lblM2.textContent = 'm²'; lblM2.style.fontSize = '12px';

  const btnRem = document.createElement('button');
  btnRem.textContent = '×'; btnRem.title = 'Remover';
  btnRem.onclick = () => row.remove();

  row.appendChild(sel); row.appendChild(inpTipo);
  row.appendChild(inpValor); row.appendChild(lblM2); row.appendChild(btnRem);
  document.getElementById('ed-areas-list').appendChild(row);
}

function coletarAreasPrivativas() {
  const areas = [];
  document.querySelectorAll('.area-privativa-row').forEach(row => {
    const sel   = row.querySelector('select');
    const inpTipo  = row.querySelectorAll('input[type=text]')[0];
    const inpValor = row.querySelectorAll('input[type=text]')[1];
    const tipo  = sel.value === '__custom__' ? inpTipo.value.trim() : sel.value;
    const valor = inpValor.value.trim();
    if (tipo && valor) areas.push({tipo, valor});
  });
  return areas;
}

// ── Modelo / Tipo / Habitabilidade ────────────────────────────
function onEdModelo(val) {
  const isPrivativo = val === 'privativo';
  document.getElementById('ed-privativo-dados').style.display  = isPrivativo ? '' : 'none';
  document.getElementById('ed-padrao-campos').style.display    = isPrivativo ? 'none' : '';
  document.getElementById('ed-privativo-comodos').style.display= isPrivativo ? '' : 'none';
  document.getElementById('ed-cobertura-row').style.display    = isPrivativo ? 'none' : '';
  document.getElementById('ed-cons-pintura-row').style.display = isPrivativo ? 'none' : '';
  document.getElementById('ed-padrao-acab-row').style.display  = isPrivativo ? 'none' : '';
  document.getElementById('ed-padrao-const-row').style.display = isPrivativo ? 'none' : '';
  document.getElementById('ed-grades-row').style.display       = isPrivativo ? 'none' : '';
  document.getElementById('ed-averbado-row').style.display     = isPrivativo ? 'none' : '';
  // Área privativa = sempre averbado
  if (isPrivativo) document.querySelector('[name=ed-averbado][value=S]').checked = true;
  // Pré-preencher área com área privativa do Gemini
  if (isPrivativo && state.dadosPDF) {
    const areas = (state.dadosPDF.areas_privativas || []);
    const ap = areas.find(a => a.tipo && a.tipo.toLowerCase().includes('privativa'));
    if (ap) document.getElementById('ed-area').value = ap.valor;
  }
}

function onEdTipo(val) {
  document.getElementById('ed-habitabilidade-section').style.display =
    (val === 'residencial' || val === 'misto') ? '' : 'none';
  document.getElementById('ed-uso-section').style.display =
    (val === 'nao_residencial' || val === 'misto') ? '' : 'none';

  // Atualizar label da observação de fotos conforme tipo
  const lbl = document.getElementById('ed-obs-fotos-label');
  if (!lbl) return;
  if (val === 'residencial')
    lbl.textContent = 'Não foi possível tirar fotos internas, o que impossibilitou a verificação das condições de habitabilidade.';
  else if (val === 'nao_residencial')
    lbl.textContent = 'Não foi possível tirar fotos internas, o que impossibilitou a verificação das condições de uso.';
  else
    lbl.textContent = 'Não foi possível tirar fotos internas, o que impossibilitou a verificação das condições de habitabilidade e uso.';
}

function onEdHab(val) {
  const motivo = document.getElementById('ed-hab-motivo');
  motivo.style.display = (val && val !== 'Habitável') ? '' : 'none';
}

function onEdUso(val) {
  const motivo = document.getElementById('ed-uso-motivo');
  motivo.style.display = (val && val !== 'Possui condições de uso') ? '' : 'none';
}

// ── Coletar campos ────────────────────────────────────────────
function coletarCamposEdificacao() {
  const modelo = document.querySelector('[name=ed-modelo]:checked')?.value || 'padrao';
  const tipo   = document.getElementById('ed-tipo').value;

  const campos = {
    modelo,
    tipo_edificacao:      tipo,
    material:             document.getElementById('ed-material')?.value || '',
    padrao_construtivo:   document.getElementById('ed-padrao-const').value,
    padrao_acabamento:    document.getElementById('ed-padrao-acab')?.value || '',
    paredes:              Array.from(document.querySelectorAll('[name=ed-parede]:checked')).map(c => c.value),
    paredes_livre:        document.getElementById('ed-parede-livre').value.trim(),
    cobertura:            document.querySelector('[name=ed-cobertura]:checked')?.value || '',
    aberturas:            Array.from(document.querySelectorAll('[name=ed-abertura]:checked')).map(c => c.value),
    grades:               document.getElementById('ed-grades')?.checked || false,
    piso:                 Array.from(document.querySelectorAll('[name=ed-piso]:checked')).map(c => c.value),
    piso_livre:           document.getElementById('ed-piso-livre').value.trim(),
    teto:                 Array.from(document.querySelectorAll('[name=ed-teto]:checked')).map(c => c.value),
    teto_livre:           document.getElementById('ed-teto-livre').value.trim(),
    conservacao_pintura:  document.querySelector('[name=ed-cons-pintura]:checked')?.value || '',
    conservacao_geral:    document.querySelector('[name=ed-cons-geral]:checked')?.value || '',
    obs_fotos:            document.getElementById('ed-obs-fotos').checked,
    habitabilidade:       document.querySelector('[name=ed-hab]:checked')?.value || '',
    habitabilidade_motivo:document.getElementById('ed-hab-motivo').value.trim(),
    condicao_uso:         document.querySelector('[name=ed-uso]:checked')?.value || '',
    condicao_uso_motivo:  document.getElementById('ed-uso-motivo').value.trim(),
    externos:             Array.from(document.querySelectorAll('[name=ed-externo]:checked')).map(c => c.value),
    // Privativo
    unidade:              document.getElementById('ed-nome').value.trim(),
    pavimento:            document.getElementById('ed-pavimento')?.value.trim() || '',
    edificio:             document.getElementById('ed-edificio')?.value.trim() || '',
    areas_privativas:     coletarAreasPrivativas(),
    comodos_texto:        document.getElementById('ed-comodos-texto')?.value.trim() || '',
    extras_texto:         document.getElementById('ed-extras-texto')?.value.trim() || '',
    // Cômodos padrão e privativo (mesmo grid, IDs diferentes)
    comodos: {},
  };

  // Cômodos modelo padrão
  for (const nome of COMODOS_LISTA) {
    const inp = document.getElementById(`ed-comodo-${cssId(nome)}`);
    const val = inp ? inp.value.trim() : '';
    if (val && val !== '0') campos.comodos[nome] = val;
  }
  // Cômodos modelo privativo (mesmo grid com prefixo diferente)
  for (const nome of COMODOS_LISTA) {
    const inp = document.getElementById(`ed-priv-comodo-${cssId(nome)}`);
    const val = inp ? inp.value.trim() : '';
    if (val && val !== '0') campos.comodos[nome] = val;
  }

  return campos;
}

// ── Prévia ────────────────────────────────────────────────────
async function gerarPreviewEdificacao(forcar=false) {
  const prev = document.getElementById('ed-preview');
  const editado = prev.dataset.manualEdit === 'true';

  // Se foi editado manualmente, confirmar antes de sobrescrever
  if (editado && !forcar) {
    const ok = confirm('O texto foi editado manualmente. Deseja regenerar e perder as alterações?');
    if (!ok) return;
  }

  const campos = coletarCamposEdificacao();
  const r = await pywebview.api.gerar_texto_edificacao(campos);
  prev.value = r || '';
  prev.dataset.manualEdit = 'false';
  const aviso = document.getElementById('ed-preview-aviso');
  if (aviso) aviso.style.display = 'none';
}

// ── Adicionar à lista ─────────────────────────────────────────
async function adicionarEdificacao() {
  const nome  = document.getElementById('ed-nome').value.trim();
  const area  = document.getElementById('ed-area').value.trim();
  const cons  = document.querySelector('[name=ed-cons-geral]:checked')?.value || '';
  const modelo= document.querySelector('[name=ed-modelo]:checked')?.value || 'padrao';
  const mat   = document.getElementById('ed-material')?.value || '';

  // Validação dos obrigatórios
  if (!nome) { alert('Informe o nome da edificação.'); return; }
  if (modelo === 'padrao' && !mat) { alert('Selecione o material da edificação.'); return; }
  if (modelo === 'padrao' && !document.getElementById('ed-padrao-const').value) { alert('Selecione o padrão construtivo.'); return; }
  if (!cons) { alert('Selecione o estado de conservação geral.'); return; }

  // Usar o texto da prévia — pode ser gerado ou editado manualmente
  const prev = document.getElementById('ed-preview');
  let obsTexto = prev.value.trim();

  // Se a prévia estiver vazia, gerar automaticamente
  if (!obsTexto) {
    const campos = coletarCamposEdificacao();
    obsTexto = await pywebview.api.gerar_texto_edificacao(campos);
    prev.value = obsTexto;
  }

  const averbado = document.querySelector('[name=ed-averbado]:checked')?.value || 'N';

  edState.lista.push({
    nome,
    averbado,
    area: area || '0',
    obs:  obsTexto,
    _label: `${nome} | ${averbado === 'S' ? 'Averbada' : 'Não Averbada'} | ${area || '0'} m²`,
  });

  atualizarListaEd();
  limparFormEdificacao();
}

function atualizarListaEd() {
  const lista   = document.getElementById('ed-lista');
  const section = document.getElementById('ed-lista-section');
  const btnCriar= document.getElementById('btn-criar-todas');

  lista.innerHTML = '';
  section.style.display = edState.lista.length ? '' : 'none';
  btnCriar.disabled     = edState.lista.length === 0;

  edState.lista.forEach((ed, i) => {
    const item = document.createElement('div');
    item.className = 'ed-lista-item';
    item.innerHTML = `
      <div class="ed-lista-item-info">
        <div class="ed-lista-item-titulo">${ed._label}</div>
        <div class="ed-lista-item-obs">${ed.obs || '(sem descrição)'}</div>
      </div>
      <button class="ed-lista-item-btn" onclick="removerEdificacao(${i})">✕</button>
    `;
    lista.appendChild(item);
  });
}

function removerEdificacao(i) {
  edState.lista.splice(i, 1);
  atualizarListaEd();
}

function limparFormEdificacao() {
  document.getElementById('ed-nome').value  = '';
  document.getElementById('ed-area').value  = '';
  document.getElementById('ed-preview').value = '';
  const aviso = document.getElementById('ed-preview-aviso');
  if (aviso) aviso.style.display = 'none';
  // Limpar checkboxes
  document.querySelectorAll('[name=ed-parede],[name=ed-abertura],[name=ed-piso],[name=ed-teto],[name=ed-externo]').forEach(c => c.checked = false);
  document.querySelectorAll('[name=ed-cobertura],[name=ed-cons-pintura],[name=ed-cons-geral],[name=ed-hab],[name=ed-uso]').forEach(r => { if (r.value === '') r.checked = true; });
  document.getElementById('ed-parede-livre').value = '';
  document.getElementById('ed-piso-livre').value   = '';
  document.getElementById('ed-teto-livre').value   = '';
  document.getElementById('ed-obs-fotos').checked  = false;
  document.getElementById('ed-hab-motivo').style.display = 'none';
  document.getElementById('ed-uso-motivo').style.display = 'none';
  if (document.getElementById('ed-material')) document.getElementById('ed-material').value = '';
  document.getElementById('ed-padrao-const').value = '';
  if (document.getElementById('ed-padrao-acab')) document.getElementById('ed-padrao-acab').value = '';
  for (const nome of COMODOS_LISTA) {
    const inp = document.getElementById(`ed-comodo-${cssId(nome)}`);
    if (inp) inp.value = '';
  }
  for (const nome of COMODOS_LISTA) {
    const inp = document.getElementById(`ed-priv-comodo-${cssId(nome)}`);
    if (inp) inp.value = '';
  }
}

// ── Criar todas ───────────────────────────────────────────────
async function criarTodasEdificacoes() {
  if (!edState.lista.length) return;
  const ok = await confirmar(
    'Confirmar Criação',
    `Criar ${edState.lista.length} edificação(ões) na vistoria?\n\nEssa ação não pode ser desfeita.`
  );
  if (!ok) return;
  fecharEdificacoes();
  const r = await pywebview.api.criar_edificacoes(edState.lista);
  if (r.ok) {
    edState.lista = [];
    atualizarListaEd();
  }
}


// ══════════════════════════════════════════════════════════════
// SISTEMAS DE CONSULTA
// ══════════════════════════════════════════════════════════════

async function abrirSistemas() {
  document.getElementById('sistemas-loading').style.display = 'block';
  document.getElementById('sistemas-lista').style.display   = 'none';
  document.getElementById('modal-sistemas').classList.add('open');

  const r = await pywebview.api.buscar_sistemas_consulta();

  document.getElementById('sistemas-loading').style.display = 'none';

  if (!r.ok) {
    document.getElementById('sistemas-lista').style.display = 'block';
    document.getElementById('sistemas-lista').innerHTML =
      `<div class="sistema-vazio">❌ ${r.msg}</div>`;
    return;
  }

  document.querySelector('#sistemas-titulo .btn-label').textContent =
    `Sistemas Disponíveis — ${r.cidade}/${r.uf}`;

  const lista    = document.getElementById('sistemas-lista');
  lista.innerHTML = '';
  lista.style.display = 'block';

  // Separar sistemas da cidade e rurais
  const daCidade = r.sistemas.filter(s => !s.rural);
  const rurais   = r.sistemas.filter(s => s.rural);

  const estaduais2 = r.sistemas.filter(s => s.estadual);
  if (daCidade.length === 0 && rurais.length === 0 && estaduais2.length === 0) {
    lista.innerHTML = `<div class="sistema-vazio">Nenhum sistema encontrado para ${r.cidade}/${r.uf}.</div>`;
    return;
  }

  if (daCidade.length > 0) {
    const sec = document.createElement('div');
    sec.className = 'sistema-secao-titulo';
    sec.textContent = `📍 Sistemas de ${r.cidade}`;
    lista.appendChild(sec);
    daCidade.forEach(s => lista.appendChild(criarCardSistema(s)));
  }

  if (rurais.length > 0) {
    const sec = document.createElement('div');
    sec.className = 'sistema-secao-titulo';
    sec.textContent = '🌿 Sistemas Rurais Globais';
    lista.appendChild(sec);
    rurais.forEach(s => lista.appendChild(criarCardSistema(s)));
  }
}

function criarCardSistema(s) {
  const card = document.createElement('div');
  card.className = 'sistema-card' + (s.rural ? ' rural' : '');

  const header = document.createElement('div');
  header.className = 'sistema-card-header';

  const tipo = document.createElement('span');
  tipo.className   = 'sistema-card-tipo';
  tipo.textContent = s.tipo;
  header.appendChild(tipo);
  card.appendChild(header);

  const link = document.createElement('div');
  link.className   = 'sistema-card-link';
  link.textContent = s.link;
  card.appendChild(link);

  // Credenciais se existirem
  if (s.login && s.login !== '-' || s.senha && s.senha !== '-') {
    const cred = document.createElement('div');
    cred.className = 'sistema-credenciais';
    if (s.login && s.login !== '-')
      cred.innerHTML += `<span>👤 Login: <strong>${s.login}</strong></span>`;
    if (s.senha && s.senha !== '-')
      cred.innerHTML += `<span>🔑 Senha: <strong>${s.senha}</strong></span>`;
    card.appendChild(cred);
  }

  // Botões
  const acoes = document.createElement('div');
  acoes.className = 'sistema-card-acoes';

  // Verificar se é "Indisponível"
  const isIndisponivel = s.link.toLowerCase().includes('indisponível') ||
                         s.link.toLowerCase().includes('indisponivel');

  if (!isIndisponivel) {
    const btnAbrir = document.createElement('button');
    btnAbrir.className   = 'btn btn-accent';
    btnAbrir.textContent = '🌐 Abrir';
    btnAbrir.onclick     = () => pywebview.api.abrir_link(s.link);
    acoes.appendChild(btnAbrir);
  } else {
    const badge = document.createElement('span');
    badge.style.cssText  = 'font-size:11px;color:#ef4444;font-weight:700;';
    badge.textContent    = '⚠️ Indisponível';
    acoes.appendChild(badge);
  }

  const btnCopiar = document.createElement('button');
  btnCopiar.className = 'btn btn-primary';
  btnCopiar.innerHTML =
    '<svg class="btn-icon" viewBox="0 0 18 18" aria-hidden="true"><rect x="4" y="3" width="10" height="13" rx="1.5"/>' +
    '<rect x="6.5" y="1.5" width="5" height="3" rx="1"/><line x1="6.5" y1="8" x2="11.5" y2="8"/>' +
    '<line x1="6.5" y1="11" x2="11.5" y2="11"/></svg><span class="btn-label">Copiar Link</span>';
  const btnCopiarLabel = btnCopiar.querySelector('.btn-label');
  btnCopiar.onclick = () => {
    navigator.clipboard.writeText(s.link).then(() => {
      btnCopiarLabel.textContent = 'Copiado!';
      setTimeout(() => btnCopiarLabel.textContent = 'Copiar Link', 2000);
    });
  };
  acoes.appendChild(btnCopiar);

  card.appendChild(acoes);
  return card;
}

function fecharSistemas() {
  document.getElementById('modal-sistemas').classList.remove('open');
}


// ══════════════════════════════════════════════════════════════
// JSON MANUAL
// ══════════════════════════════════════════════════════════════

async function abrirJsonManual() {
  document.getElementById('json-tipo-laudo-wrap').style.display  = state.modoMultiplo ? 'none' : '';
  document.getElementById('json-multiplo-aviso').style.display   = state.modoMultiplo ? '' : 'none';
  if (!state.modoMultiplo) {
    // Carregar prompt do tipo selecionado
    const tipo = document.querySelector('[name=tipo-laudo]:checked')?.value || 'normal';
    document.querySelector('[name=json-tipo][value=' + tipo + ']').checked = true;
  }
  await carregarPromptJson();
  document.getElementById('json-manual-input').value = '';
  document.getElementById('json-erro').style.display = 'none';
  document.getElementById('modal-json-manual').classList.add('open');
}

async function carregarPromptJson() {
  const tipo   = state.modoMultiplo ? 'multiplo' : (document.querySelector('[name=json-tipo]:checked')?.value || 'normal');
  const prompt = await pywebview.api.get_prompt(tipo);
  document.getElementById('json-prompt-display').value = prompt;
}

// Atualizar prompt ao mudar tipo
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[name=json-tipo]').forEach(r => {
    r.addEventListener('change', carregarPromptJson);
  });
});

function copiarPrompt() {
  const txt = document.getElementById('json-prompt-display').value;
  navigator.clipboard.writeText(txt).then(() => {
    const label = event.currentTarget.querySelector('.btn-label');
    label.textContent = 'Copiado!';
    setTimeout(() => label.textContent = 'Copiar', 2000);
  });
}

function fecharJsonManual() {
  document.getElementById('modal-json-manual').classList.remove('open');
}

async function processarJsonManual() {
  // Salvar código e contador igual ao Gemini
  state.codigoNoMomento = document.getElementById('input-busca').value.trim();
  state.pdfsAnalisados++;
  const jsonStr = document.getElementById('json-manual-input').value.trim();
  const erroEl  = document.getElementById('json-erro');

  if (!jsonStr) {
    erroEl.textContent   = '❌ Cole o JSON antes de processar.';
    erroEl.style.display = 'block';
    return;
  }

  erroEl.style.display = 'none';

  let r;
  if (state.modoMultiplo) {
    r = await pywebview.api.processar_json_manual_multiplo(jsonStr);
  } else {
    const tipo = document.querySelector('[name=json-tipo]:checked')?.value || 'normal';
    state.tipoLaudo = tipo;
    r = await pywebview.api.processar_json_manual(jsonStr, tipo);
  }

  if (!r.ok) {
    erroEl.textContent   = `❌ ${r.msg}`;
    erroEl.style.display = 'block';
    return;
  }

  fecharJsonManual();
  addLog('✅ JSON manual processado com sucesso!');
}

async function carregarAnaliseSalva() {
  // Salvar código e contador igual ao Gemini
  state.codigoNoMomento = document.getElementById('input-busca').value.trim();
  state.pdfsAnalisados++;

  const r = await pywebview.api.carregar_analise_salva();

  if (!r.ok) {
    addLog(`⚠️ ${r.msg}`);
    return;
  }

  if (r.tipo && r.tipo !== 'multiplo') {
    state.tipoLaudo = r.tipo;
  }
  addLog('✅ Análise salva carregada com sucesso!');
}


// ══════════════════════════════════════════════════════════════
// CONFIGURAÇÕES — PASTA CUBS E ABA PLANILHA
// ══════════════════════════════════════════════════════════════

async function selecionarPastaCubs() {
  const r = await pywebview.api.selecionar_pasta_cubs();
  if (r.ok) {
    const nome = r.pasta.split('\\').pop() || r.pasta.split('/').pop();
    setStatus('status-cubs', `🗂️ CUBs: ${nome}`, 'ok');
  }
}

function abrirConfigAba() {
  pywebview.api.get_config().then(cfg => {
    document.getElementById('input-aba').value = cfg.aba_planilha || '';
  });
  document.getElementById('modal-aba').classList.add('open');
}

function fecharConfigAba() {
  document.getElementById('modal-aba').classList.remove('open');
}

async function salvarAba() {
  const val = document.getElementById('input-aba').value.trim();
  if (!val) return;
  await pywebview.api.salvar_config_texto('aba_planilha', val);
  setStatus('status-aba', `📋 Aba: ${val}`, 'ok');
  fecharConfigAba();
}

// ── Trocar Nome das Fotos ─────────────────────────────────────
let _trocarFotosItens = [];

async function abrirTrocarFotos() {
  _trocarFotosItens = [];
  const loading  = document.getElementById('trocar-fotos-loading');
  const conteudo = document.getElementById('trocar-fotos-conteudo');
  loading.textContent = 'Carregando fotos da vistoria...';
  loading.style.display = '';
  conteudo.style.display = 'none';
  document.getElementById('trocar-fotos-novo-nome').value = '';
  document.getElementById('modal-trocar-fotos').classList.add('open');

  const timeout = setTimeout(() => {
    loading.textContent = '⚠️ Tempo esgotado ao carregar fotos. Verifique o log do sistema.';
  }, 15000);

  try {
    const res = await pywebview.api.get_fotos_vistoria();
    clearTimeout(timeout);

    if (!res || !res.ok) {
      loading.textContent = '⚠️ ' + (res?.erro || 'Erro desconhecido ao carregar fotos.');
      return;
    }

    _trocarFotosItens = res.itens;
    const lista = document.getElementById('trocar-fotos-lista');
    lista.innerHTML = '';

    if (!_trocarFotosItens.length) {
      lista.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:16px;">Nenhuma foto encontrada na vistoria.</p>';
    } else {
      // Agrupar por grupo
      const grupos = {};
      _trocarFotosItens.forEach((it, idx) => {
        if (!grupos[it.grupo]) grupos[it.grupo] = [];
        grupos[it.grupo].push({...it, idx});
      });

      let grupoIdx = 0;
      for (const [nomeGrupo, fotos] of Object.entries(grupos)) {
        const idGrupo = grupoIdx++;
        // Contar nomes repetidos para sequencial
        const contagem = {};
        fotos.forEach(f => { contagem[f.nome] = (contagem[f.nome] || 0) + 1; });
        const vistos = {};
        const idxsDoGrupo = fotos.map(f => f.idx);

        // Cabeçalho do grupo: checkbox "selecionar todos" + nome do grupo editável
        const header = document.createElement('div');
        header.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 4px 4px;border-top:1px solid var(--border);margin-top:4px;';
        const cbGrupo = document.createElement('input');
        cbGrupo.type = 'checkbox';
        cbGrupo.style.cssText = 'width:15px;height:15px;cursor:pointer;flex-shrink:0;';
        cbGrupo.dataset.grupo = nomeGrupo;
        cbGrupo.addEventListener('change', () => {
          idxsDoGrupo.forEach(i => {
            const cb = document.querySelector(`.trocar-foto-cb[data-idx="${i}"]`);
            if (cb) cb.checked = cbGrupo.checked;
          });
        });
        const inputGrupo = document.createElement('input');
        inputGrupo.type = 'text';
        inputGrupo.className = 'trocar-grupo-input';
        inputGrupo.dataset.idGrupo = idGrupo;
        inputGrupo.dataset.original = nomeGrupo;
        inputGrupo.value = nomeGrupo;
        inputGrupo.style.cssText = 'flex:1;font-weight:700;font-size:13px;color:var(--text);padding:4px 7px;border:1px solid var(--border);border-radius:5px;background:var(--bg-input, #fff);';
        header.appendChild(cbGrupo);
        header.appendChild(inputGrupo);
        lista.appendChild(header);

        // Fotos do grupo
        fotos.forEach(f => {
          vistos[f.nome] = (vistos[f.nome] || 0) + 1;
          const nomeBase = contagem[f.nome] > 1 ? `${f.nome} (${vistos[f.nome]})` : f.nome;
          _trocarFotosItens[f.idx].nome_display = nomeBase;
          _trocarFotosItens[f.idx].id_grupo_ui = idGrupo;

          const row = document.createElement('div');
          row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:4px 4px 4px 20px;';

          const cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.className = 'trocar-foto-cb';
          cb.dataset.idx = f.idx;
          cb.style.cssText = 'width:15px;height:15px;cursor:pointer;flex-shrink:0;';
          // Ao desmarcar um filho, coloca grupo em indeterminate
          cb.addEventListener('change', () => {
            const todos   = idxsDoGrupo.map(i => document.querySelector(`.trocar-foto-cb[data-idx="${i}"]`));
            const marcados = todos.filter(c => c?.checked).length;
            cbGrupo.checked       = marcados === todos.length;
            cbGrupo.indeterminate = marcados > 0 && marcados < todos.length;
          });

          const input = document.createElement('input');
          input.type = 'text';
          input.className = 'trocar-foto-input';
          input.dataset.idx = f.idx;
          input.value = f.nome;
          input.style.cssText = 'flex:1;padding:4px 7px;border:1px solid var(--border);border-radius:5px;font-size:13px;background:var(--bg-input, #fff);color:var(--text);';

          row.appendChild(cb);
          row.appendChild(input);
          lista.appendChild(row);
        });
      }
    }

    loading.style.display = 'none';
    conteudo.style.display = '';
  } catch(err) {
    clearTimeout(timeout);
    loading.textContent = '⚠️ Erro ao carregar: ' + err;
  }
}

function fecharTrocarFotos() {
  document.getElementById('modal-trocar-fotos').classList.remove('open');
}

function trocarFotosSelecionarTodos(marcar) {
  document.querySelectorAll('.trocar-foto-cb').forEach(cb => { cb.checked = marcar; cb.dispatchEvent(new Event('change')); });
  document.querySelectorAll('[data-grupo]').forEach(cb => { cb.checked = marcar; cb.indeterminate = false; });
}

async function confirmarTrocarFotos() {
  const novoNomeGlobal = document.getElementById('trocar-fotos-novo-nome').value.trim();

  const selecionados = Array.from(document.querySelectorAll('.trocar-foto-cb:checked'))
    .map(cb => {
      const idx  = parseInt(cb.dataset.idx);
      const item = _trocarFotosItens[idx];

      // Se o nome do grupo foi editado (diferente do original), vale para todas
      // as fotos marcadas daquele grupo, sobrescrevendo o nome individual.
      let nomeIndividual = null;
      if (item.id_grupo_ui !== undefined) {
        const inputGrupoEl = document.querySelector(`.trocar-grupo-input[data-id-grupo="${item.id_grupo_ui}"]`);
        if (inputGrupoEl) {
          const valGrupo  = inputGrupoEl.value.trim();
          const original  = (inputGrupoEl.dataset.original || '').trim();
          if (valGrupo && valGrupo !== original) {
            nomeIndividual = valGrupo;
          }
        }
      }

      // Sem edição no grupo — usa o valor do input editável daquela foto
      if (nomeIndividual === null) {
        const inputEl = document.querySelector(`.trocar-foto-input[data-idx="${idx}"]`);
        nomeIndividual = inputEl ? inputEl.value.trim() : item.nome;
      }

      return { reg_grupo: item.reg_grupo, reg_img: item.reg_img, nome_individual: nomeIndividual };
    });

  if (!selecionados.length) { alert('Selecione ao menos uma foto.'); return; }
  if (!novoNomeGlobal && selecionados.some(f => !f.nome_individual)) {
    alert('Algumas fotos estão sem nome. Preencha o campo global ou edite os nomes individuais.');
    return;
  }

  // Fechar modal imediatamente e rodar em background
  fecharTrocarFotos();
  addLog(`⏳ Renomeando ${selecionados.length} foto(s)...`);

  pywebview.api.renomear_fotos_selecionadas(selecionados, novoNomeGlobal).then(res => {
    if (res && res.ok) {
      addLog(`✅ ${res.total} foto(s) renomeada(s)${novoNomeGlobal ? ` para "${novoNomeGlobal}"` : ' com nomes individuais'}`);
    } else {
      addLog(`⚠️ Erro ao renomear: ${(res && res.erro) || 'desconhecido'}`);
    }
  }).catch(err => {
    addLog(`⚠️ Erro ao renomear: ${err}`);
  });
}
