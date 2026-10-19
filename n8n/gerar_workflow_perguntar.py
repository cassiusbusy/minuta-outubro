"""Gera o workflow n8n "Minuta PD — Pergunte à Minuta" (v2: varredura completa + Claude).

Lê a minuta inteira do Qdrant a cada pergunta (alias minuta_ultima_versao, gravado pelo notebook
IPPUC_INGESTAO_MINUTA_OUTUBRO_2026), faz uma varredura literal determinística em todos os artigos
e entrega ao Claude a minuta completa (em cache) + a lista do que a varredura achou.

Fluxo:
  Webhook → Configurar e Validar → Ler Minuta (Qdrant scroll) → Ler Glossário (Qdrant scroll)
  → Varredura Completa (código, determinístico) → Montar Pedido → Claude → Formatar Resposta → Responder
"""
import json, re, uuid, sys, os
from collections import deque

SAIDA = sys.argv[1] if len(sys.argv) > 1 else '.'
QDRANT_URL = 'https://01232c5c-728d-4190-a9ad-5e2cb71dad5d.europe-west3-0.gcp.cloud.qdrant.io'

_pos = [0]


def _xy():
    _pos[0] += 230
    return [_pos[0], 300]


def node(name, tipo, versao, params, **extra):
    n = {"parameters": params, "id": str(uuid.uuid4()), "name": name, "type": tipo,
         "typeVersion": versao, "position": _xy()}
    n.update(extra)
    return n


def code(name, js, notes=None):
    extra = {"notes": notes, "notesInFlow": True} if notes else {}
    return node(name, "n8n-nodes-base.code", 2, {"jsCode": js.strip() + "\n"}, **extra)


def http(name, url, body_expr, notes=None, headers=None, timeout=None):
    p = {"method": "POST", "url": url, "authentication": "genericCredentialType",
         "genericAuthType": "httpHeaderAuth", "sendBody": True, "specifyBody": "json",
         "jsonBody": body_expr, "options": {}}
    if headers:
        p["sendHeaders"] = True
        p["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in headers.items()]}
    if timeout:
        p["options"]["timeout"] = timeout
    extra = {"notes": notes, "notesInFlow": True} if notes else {}
    return node(name, "n8n-nodes-base.httpRequest", 4.2, p, **extra)


N_HOOK = 'Webhook Pergunta'
N_CFG = 'Configurar e Validar'
N_MIN = 'Ler Minuta Completa'
N_GLO = 'Ler Glossário'
N_VAR = 'Varredura Completa'
N_PED = 'Montar Pedido'
N_LLM = 'Claude'
N_FMT = 'Formatar Resposta'
N_RESP = 'Responder'
N_ERRO = 'Responder Erro'

JS_CFG = r"""
// ==== CONFIGURE AQUI ====
const QDRANT_URL = '%s';
const COLECAO_MINUTA = 'minuta_ultima_versao';      // alias criado pelo notebook de ingestão
const COLECAO_GLOSSARIO = 'glossario_ultima_versao';
const MODELO = 'claude-sonnet-5-5';
const MAX_TOKENS_RESPOSTA = 16000;   // trocas na minuta toda podem listar dezenas de artigos
// ========================

const corpo = $input.first().json.body || {};
const pergunta = String(corpo.pergunta || '').trim();
if (pergunta.length < 4) throw new Error('Pergunta vazia ou curta demais.');
if (pergunta.length > 2000) throw new Error('Pergunta longa demais (máx. 2000 caracteres).');

return [{ json: {
  qdrant_url: QDRANT_URL.replace(/\/+$/, ''), colecao_minuta: COLECAO_MINUTA, colecao_glossario: COLECAO_GLOSSARIO,
  modelo: MODELO, max_tokens: MAX_TOKENS_RESPOSTA, pergunta,
} }];
""" % QDRANT_URL

# Varredura determinística: não depende de IA, então nunca "esquece" artigo.
JS_VAR = r"""
const cfg = $('%s').first().json;
const pontos = ($('%s').first().json.result || {}).points || [];
const glossPts = ($('%s').first().json.result || {}).points || [];
if (pontos.length < 50) throw new Error('A coleção da minuta voltou com ' + pontos.length + ' artigos. Confira o alias no Qdrant.');

const arts = pontos.map((p) => p.payload).filter((p) => p && p.numero != null)
  .sort((a, b) => a.numero - b.numero);
const porNum = new Map(arts.map((a) => [Number(a.numero), a]));
const gloss = glossPts.map((p) => p.payload).filter((g) => g && g.termo);

// ---------- normalização (sem acento, minúsculas) ----------
const semAc = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const L = '[\\p{L}\\p{N}]';
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function contar(termo, texto) {
  const t = semAc(termo).trim().replace(/\s+/g, ' ');
  if (t.length < 2) return 0;
  const re = new RegExp('(?<!' + L + ')' + esc(t).replace(/ /g, '\\s+') + '(?!' + L + ')', 'gu');
  return (semAc(texto).match(re) || []).length;
}
const textoN = new Map(arts.map((a) => [a.numero, semAc(a.texto_artigo)]));
function varrer(termo) {
  const achados = [];
  for (const a of arts) { const c = contar(termo, a.texto_artigo); if (c) achados.push({ artigo: a.numero, ocorrencias: c }); }
  return achados;
}

const q = cfg.pergunta;
// 1) artigos citados pelo número: "art. 45", "artigo 45", "arts. 10 e 12"
const alvos = new Set();
for (const m of q.matchAll(/\b(?:arts?\.?|artigos?)\s*((?:\d+\s*[º°o]?\s*(?:,|e|a|ou)?\s*)+)/gi)) {
  for (const n of m[1].match(/\d+/g) || []) if (porNum.has(Number(n))) alvos.add(Number(n));
}
// 2) termos entre aspas: "poderá", “deverá”, 'segurança viária', «...»
const termos = new Set();
for (const m of q.matchAll(/["“”'‘’«»]([^"“”'‘’«»]{2,80})["“”'‘’«»]/g)) termos.add(m[1].trim());

// 3) para cada artigo-alvo: quem remete a ele, mesma seção e conceitos que ele usa
const remissoes = [];
const mesmaSecao = [];
const conceitos = new Map();
const MINUSC = new Set(['de', 'da', 'do', 'das', 'dos', 'e', 'para', 'em', 'a', 'o']);
const GENERICOS = new Set(['plano diretor', 'lei federal', 'lei municipal', 'lei complementar', 'poder publico', 'municipio', 'poder executivo', 'curitiba', 'estatuto da cidade', 'constituicao federal']);
for (const n of alvos) {
  const a = porNum.get(n);
  // remissões internas: "art. 45" sem "da Lei/do Decreto..." logo depois
  const reRem = new RegExp('\\b(?:arts?\\.?|artigos?)\\s*(?:\\d+\\s*[º°o]?\\s*(?:,|e|a|ou)\\s*)*' + n + '(?!\\d)(?:\\s*[º°o])?(?![^.;]{0,40}\\b(?:da|do)\\s+(?:Lei|Decreto|Constitui|Estatuto|C[óo]digo))', 'i');
  for (const b of arts) if (b.numero !== n && reRem.test(b.texto_artigo)) remissoes.push({ alvo: n, artigo: b.numero });
  for (const b of arts) if (b.numero !== n && b.breadcrumb && b.breadcrumb === a.breadcrumb) mesmaSecao.push({ alvo: n, artigo: b.numero });
  // conceitos do alvo: termos do glossário presentes nele, siglas e nomes próprios compostos
  const cand = new Set();
  for (const g of gloss) { const base = String(g.termo).replace(/\s*\(.*?\)\s*$/, '').trim(); if (contar(base, a.texto_artigo)) cand.add(base); }
  for (const m of a.texto_artigo.matchAll(/\b[A-ZÁÉÍÓÚÂÊÔÃÕÇ]{3,}s?\b/g)) cand.add(m[0]);
  const palavras = a.texto_artigo.replace(/^Art\.\s*\d+\S*\s*/, '').split(/(\s+|[,;:.()])/);
  let atual = [];
  const fechar = () => {
    while (atual.length && MINUSC.has(atual[atual.length - 1].toLowerCase())) atual.pop();
    const maius = atual.filter((w) => /^[A-ZÁÉÍÓÚÂÊÔÃÕÇ]/.test(w)).length;
    if (maius >= 2) cand.add(atual.join(' '));
    atual = [];
  };
  for (const w of palavras) {
    if (!w.trim()) continue;
    if (/^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\p{L}-]+$/u.test(w)) atual.push(w);
    else if (atual.length && MINUSC.has(w.toLowerCase())) atual.push(w);
    else fechar();
  }
  fechar();
  for (const c of cand) {
    if (/^(Art|Lei|Inciso|Parágrafo|Este|Esta|Os|As)$/i.test(c)) continue;
    if (GENERICOS.has(semAc(c))) continue;
    const ach = varrer(c).filter((x) => x.artigo !== n);
    // conceito presente em muitos artigos é genérico demais para indicar impacto
    if (ach.length && ach.length <= 25) conceitos.set(c, { conceito: c, alvo: n, artigos: ach });
  }
}

// Pergunta de alteração ("trocar 'poderá' por 'deverá' no art. 63"): os termos entre aspas são a edição,
// não o tema. Eles são contados (para mostrar o uso no resto da minuta), mas não entram no checklist.
// Troca explícita: "trocar/mudar "X" por/para "Y"". X = termo original (onde a troca acontece), Y = termo novo.
const troca = q.match(/(?:troc|mud|substitu|alter|modific)[^"“”']{0,60}["“']([^"“”']{1,60})["”'][^"“”']{0,25}?\b(?:por|para|pelo|pela)\s+["“']([^"“”']{1,60})["”']/i);
const termoOriginal = troca ? troca[1].trim() : null;
const termoNovo = troca ? troca[2].trim() : null;
const ehAlteracao = !!troca || (alvos.size > 0 && /\b(troc|mud|substitu|alter|modific|passa[rn]?\b|reda[çc][ãa]o|emenda)/i.test(q));
const listaTermos = [...termos].map((t) => {
  const ach = varrer(t);
  let papel = 'tema';
  if (termoOriginal && semAc(t) === semAc(termoOriginal)) papel = 'original';
  else if (termoNovo && semAc(t) === semAc(termoNovo)) papel = 'novo';
  else if (ehAlteracao) papel = 'edicao';
  return { termo: t, papel, total: ach.reduce((s, x) => s + x.ocorrencias, 0), artigos: ach, edicao: papel !== 'tema',
           no_alvo: [...alvos].map((n) => ({ artigo: n, ocorrencias: contar(t, porNum.get(n).texto_artigo) })) };
});

// 4) conjunto completo a ser avaliado (checklist)
const motivos = new Map();
const marcar = (n, m) => { if (!motivos.has(n)) motivos.set(n, new Set()); motivos.get(n).add(m); };
for (const n of alvos) marcar(n, 'artigo citado na pergunta');
for (const r of remissoes) marcar(r.artigo, 'remete ao Art. ' + r.alvo);
for (const s of mesmaSecao) marcar(s.artigo, 'mesma seção do Art. ' + s.alvo);
for (const c of conceitos.values()) for (const x of c.artigos) marcar(x.artigo, 'usa "' + c.conceito + '"');
for (const t of listaTermos) {
  if (t.papel === 'tema') for (const x of t.artigos) marcar(x.artigo, 'contém "' + t.termo + '"');
  // troca na minuta toda (sem artigo citado): todo artigo com o termo original muda de redação
  if (t.papel === 'original' && !alvos.size) for (const x of t.artigos) marcar(x.artigo, 'tem "' + t.termo + '" (' + x.ocorrencias + 'x), que seria trocado por "' + termoNovo + '"');
}
const checklist = [...motivos.entries()].sort((a, b) => a[0] - b[0]).map(([n, m]) => ({ artigo: n, motivos: [...m] }));

return [{ json: {
  pergunta: q,
  alvos: [...alvos],
  eh_alteracao: ehAlteracao,
  troca: troca ? { original: termoOriginal, novo: termoNovo, escopo: alvos.size ? 'artigos citados' : 'minuta inteira' } : null,
  termos: listaTermos,
  remissoes, mesma_secao: mesmaSecao,
  conceitos: [...conceitos.values()].map((c) => ({ conceito: c.conceito, alvo: c.alvo, artigos: c.artigos.map((x) => x.artigo) })),
  checklist,
  total_artigos_minuta: arts.length,
  // texto completo, em ordem fixa (a ordem estável é o que permite o cache do prompt)
  minuta_texto: arts.map((a) => (a.breadcrumb ? '[' + a.breadcrumb + ']\n' : '') + a.texto_artigo).join('\n\n'),
  glossario_texto: gloss.map((g) => g.termo + ': ' + g.definicao).join('\n'),
} }];
""" % (N_CFG, N_MIN, N_GLO)

JS_PED = r"""
const cfg = $('%s').first().json;
const v = $input.first().json;

const instrucoes = [
  'Você apoia a Comissão da Câmara Municipal de Curitiba na análise do Projeto de Lei de revisão do Plano Diretor.',
  'Você recebe a MINUTA COMPLETA e o GLOSSÁRIO. Responda em português, com base SOMENTE nesse texto.',
  'Cite sempre o artigo (e inciso/parágrafo) no formato "Art. 45, § 2º, III".',
  'Não emita opinião sobre o mérito político das propostas; descreva efeitos jurídicos e de redação.',
  'Quando a pergunta for sobre ALTERAR um texto (ex.: trocar "poderá" por "deverá"), explique: o que muda no próprio artigo',
  '(faculdade vira obrigação, quem passa a ser obrigado, se surge prazo ou necessidade de regulamentação/orçamento),',
  'e quais outros artigos são afetados: os que remetem a ele, os que tratam do mesmo instrumento/conceito,',
  'os que ficariam contraditórios ou redundantes, e os que dependem dele.',
  'Você recebe também uma VARREDURA feita por programa em todos os artigos. Avalie TODOS os artigos da lista "checklist",',
  'sem pular nenhum, e acrescente outros que você identificar lendo a minuta (marque origem "leitura").',
  'Se a varredura indicar que um termo da alteração não aparece no artigo citado, avise o usuário antes de analisar.',
  'Se nada for afetado, diga isso explicitamente.',
  'Quando a troca for na minuta inteira, a resposta deve dizer quantos artigos e ocorrências mudam e agrupar os efeitos por tipo',
  '(ex.: competências do Executivo que viram obrigação, faculdades de particulares que viram dever, exceções que viram regra),',
  'destacando os de maior impacto. Atenção a construções como "não poderá", que viraria "não deverá" e mudaria o sentido.',
  'Seja conciso: "motivo" com no máximo 15 palavras. Comece o JSON pelo campo "resposta".',
  'Responda APENAS com um JSON válido, sem texto antes ou depois, neste formato:',
  '{"resposta": "texto corrido, objetivo", "impactos": [{"artigo": 45, "nivel": "direto|indireto|sem impacto", "origem": "varredura|leitura", "motivo": "uma frase"}], "artigos": [45, 46]}',
].join('\n');

const varredura = {
  artigos_citados: v.alvos,
  pergunta_de_alteracao: v.eh_alteracao,
  troca_proposta: v.troca,
  termos_entre_aspas: v.termos.map((t) => ({ termo: t.termo, papel: { tema: 'tema buscado', original: 'termo que seria substituído', novo: 'termo novo (só contado)', edicao: 'texto da alteração proposta' }[t.papel],
    ocorrencias_no_artigo_citado: t.no_alvo, total_na_minuta: t.total, artigos: t.artigos.map((x) => x.artigo) })),
  remissoes_aos_artigos_citados: v.remissoes,
  mesma_secao: v.mesma_secao,
  conceitos_compartilhados: v.conceitos,
  checklist: v.checklist,
};

const body = {
  model: cfg.modelo,
  max_tokens: cfg.max_tokens,
  system: [
    { type: 'text', text: instrucoes },
    // Bloco grande e estável: fica em cache por 1 hora; as perguntas seguintes leem a minuta do cache.
    { type: 'text', text: 'MINUTA COMPLETA (' + v.total_artigos_minuta + ' artigos):\n\n' + v.minuta_texto + '\n\nGLOSSÁRIO:\n' + v.glossario_texto,
      cache_control: { type: 'ephemeral', ttl: '1h' } },
  ],
  messages: [{ role: 'user', content:
    'PERGUNTA:\n' + v.pergunta + '\n\nVARREDURA AUTOMÁTICA (todos os artigos foram percorridos):\n' + JSON.stringify(varredura) },
  ],
};
return [{ json: { body } }];
""" % N_CFG

JS_FMT = r"""
const v = $('%s').first().json;
const r = $input.first().json;
const bruto = (r.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('');
const parada = r.stop_reason || null;
let saida;
try {
  const ini = bruto.indexOf('{'), fim = bruto.lastIndexOf('}');
  saida = JSON.parse(bruto.slice(ini, fim + 1));
} catch (e) {
  // JSON cortado (limite de tamanho): recupera o que der da resposta e dos impactos já escritos
  const res = bruto.match(/"resposta"\s*:\s*"((?:[^"\\]|\\.)*)"/);
  let texto = '';
  try { texto = res ? JSON.parse('"' + res[1] + '"') : ''; } catch (e2) { texto = res ? res[1] : ''; }
  const imp = [...bruto.matchAll(/\{\s*"artigo"\s*:\s*(\d+)\s*,\s*"nivel"\s*:\s*"([^"]+)"\s*,\s*"origem"\s*:\s*"([^"]+)"\s*,\s*"motivo"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}/g)]
    .map((m) => ({ artigo: Number(m[1]), nivel: m[2], origem: m[3], motivo: m[4] }));
  saida = { resposta: texto || (imp.length ? '' : bruto), impactos: imp, artigos: [] };
}
let aviso = '';
if (parada === 'max_tokens') aviso = 'A resposta da IA passou do limite de tamanho e foi cortada; o que veio está abaixo. Para perguntas muito amplas, aumente MAX_TOKENS_RESPOSTA ou divida a pergunta.';
else if (!bruto.trim()) aviso = 'A IA não devolveu texto (motivo de parada: ' + (parada || 'desconhecido') + ').';

const existe = new Set(v.checklist.map((c) => c.artigo));
const total = v.total_artigos_minuta;
const valido = (n) => Number.isInteger(n) && n >= 1 && n <= total;
// um artigo por linha: se a IA repetiu, fica a avaliação mais grave
const peso = { 'direto': 0, 'indireto': 1, 'sem impacto': 2 };
const porArt = new Map();
for (const i of (saida.impactos || []).map((i) => ({ ...i, artigo: Number(i.artigo) })).filter((i) => valido(i.artigo))) {
  const ant = porArt.get(i.artigo);
  if (!ant || (peso[i.nivel] ?? 3) < (peso[ant.nivel] ?? 3)) porArt.set(i.artigo, i);
}
const impactos = [...porArt.values()];
const avaliados = new Set(impactos.map((i) => i.artigo));
const naoAvaliados = [...existe].filter((n) => !avaliados.has(n));
let usados = [...new Set([...(saida.artigos || []).map(Number), ...impactos.filter((i) => i.nivel !== 'sem impacto').map((i) => i.artigo)])].filter(valido).sort((a, b) => a - b);

return [{ json: {
  pergunta: v.pergunta,
  resposta: [aviso, String(saida.resposta || '').trim()].filter(Boolean).join('\n\n') || 'Não consegui formular uma resposta.',
  parada,
  impactos: impactos.sort((a, b) => a.artigo - b.artigo),
  fontes: usados.map((n) => ({ artigo: n })),
  varredura: {
    total_artigos_percorridos: total,
    artigos_citados: v.alvos,
    termos: v.termos.map((t) => ({ termo: t.termo, papel: t.papel, total: t.total, artigos: t.artigos.map((x) => x.artigo), no_alvo: t.no_alvo })),
    checklist: v.checklist,
    nao_avaliados_pela_ia: naoAvaliados,
  },
  uso: r.usage || null,
} }];
""" % N_VAR

CFG = f"$('{N_CFG}').first().json"


def build():
    nodes = [
        node(N_HOOK, "n8n-nodes-base.webhook", 2, {
            "httpMethod": "POST", "path": "minuta-perguntar", "responseMode": "responseNode",
            "options": {"allowedOrigins": "*"},
        }, webhookId=str(uuid.uuid4())),
        code(N_CFG, JS_CFG, notes="QDRANT_URL, alias e modelo ficam aqui"),
        http(N_MIN, f"={{{{ {CFG}.qdrant_url }}}}/collections/{{{{ {CFG}.colecao_minuta }}}}/points/scroll",
             '={{ JSON.stringify({ limit: 2000, with_payload: ["numero", "codigo", "breadcrumb", "texto_artigo"], with_vector: false }) }}',
             notes="Credencial: Qdrant. Traz os 355 artigos, sempre."),
        http(N_GLO, f"={{{{ {CFG}.qdrant_url }}}}/collections/{{{{ {CFG}.colecao_glossario }}}}/points/scroll",
             '={{ JSON.stringify({ limit: 500, with_payload: ["termo", "definicao"], with_vector: false }) }}',
             notes="Credencial: Qdrant"),
        code(N_VAR, JS_VAR, notes="Varre todos os artigos, sem IA"),
        code(N_PED, JS_PED),
        http(N_LLM, "https://api.anthropic.com/v1/messages", "={{ JSON.stringify($json.body) }}",
             notes="Credencial: Anthropic (Header Auth x-api-key)",
             headers={"anthropic-version": "2023-06-01"}, timeout=180000),
        code(N_FMT, JS_FMT),
        node(N_RESP, "n8n-nodes-base.respondToWebhook", 1.1, {
            "respondWith": "json", "responseBody": "={{ JSON.stringify($json) }}",
            "options": {"responseHeaders": {"entries": [{"name": "Access-Control-Allow-Origin", "value": "*"}]}},
        }),
    ]
    nodes.append({"parameters": {"content": f"""## Pergunte à Minuta (v2: varredura completa + Claude)

**Fonte:** o alias `minuta_ultima_versao` do notebook de ingestão. Quando você ingerir uma minuta nova e o alias mudar, este fluxo passa a usá-la sem edição.

**Antes de ativar**
1. Credencial **Qdrant** (Header Auth, `api-key`) em **{N_MIN}** e **{N_GLO}**.
2. Credencial **Anthropic** (Header Auth, Name `x-api-key`, Value = chave da Anthropic) em **{N_LLM}**. O header `anthropic-version` já está no node.
3. Confira `QDRANT_URL` e `MODELO` em **{N_CFG}**.
4. Ative. URL: `https://n8n.planodiretorcuritiba.cloud/webhook/minuta-perguntar`.

**Como responde**
A cada pergunta, lê os 355 artigos e o glossário, varre tudo por programa (artigos citados, termos entre aspas, remissões, mesma seção, conceitos compartilhados) e manda ao Claude a minuta inteira + a lista da varredura. A minuta fica em cache por 1 h.
""", "height": 460, "width": 600}, "id": str(uuid.uuid4()), "name": "Leia-me",
                  "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [220, -200]})
    # Qualquer falha vai para "Responder Erro", que devolve ao site o nó e a mensagem
    # (sem isso o n8n responde 200 com corpo vazio e o site não sabe o que houve).
    nodes.append(node(N_ERRO, "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "json",
        "responseBody": "={{ JSON.stringify({ erro: (($json.error && ($json.error.message || $json.error)) || 'Falha no fluxo'), no: $prevNode.name, detalhe: ($json.error && $json.error.description) || null }) }}",
        "options": {"responseHeaders": {"entries": [{"name": "Access-Control-Allow-Origin", "value": "*"}]}},
    }))
    nodes[-1]["position"] = [1150, 560]
    ordem = [N_HOOK, N_CFG, N_MIN, N_GLO, N_VAR, N_PED, N_LLM, N_FMT, N_RESP]
    conns = {}
    for a, b in zip(ordem, ordem[1:]):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
        if a != N_HOOK:
            conns[a]["main"].append([{"node": N_ERRO, "type": "main", "index": 0}])
    for n in nodes:
        if n["name"] in ordem[1:-1]:
            n["onError"] = "continueErrorOutput"
    return {"name": "Minuta PD — Pergunte à Minuta (v2)", "nodes": nodes, "connections": conns,
            "active": False, "settings": {"executionOrder": "v1"}, "pinData": {}}


def validar(wf):
    nomes = {n["name"] for n in wf["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"}
    vistos, fila = {N_HOOK}, deque([N_HOOK])
    while fila:
        for saida in wf["connections"].get(fila.popleft(), {}).get("main", []):
            for c in saida:
                if c["node"] not in vistos:
                    vistos.add(c["node"]); fila.append(c["node"])
    assert not (nomes - vistos), nomes - vistos
    refs = set(re.findall(r"\$\('([^']+)'\)", json.dumps(wf, ensure_ascii=False)))
    assert not (refs - nomes), refs - nomes
    return len(nomes)


if __name__ == '__main__':
    wf = build()
    n = validar(wf)
    caminho = os.path.join(SAIDA, 'minuta_n8n_perguntar.json')
    with open(caminho, 'w', encoding='utf-8') as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=2)
    print(f'{caminho}: {n} nós, conexões e referências válidas')
