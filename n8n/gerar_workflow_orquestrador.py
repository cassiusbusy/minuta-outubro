"""Gera o workflow n8n "Minuta PD — Pergunte usando I.A. (orquestrador)".

Endereço próprio (/webhook/minuta-perguntar-orq): roda junto com os outros dois fluxos. Mesmo formato de resposta.

  Webhook → Configurar → Buscar no Cache (Postgres) → Tem cache?
     sim → Resposta do Cache → Responder → Registrar Consulta
     não → Ler Minuta → Ler Glossário → Varredura Completa → Classificar e Responder → Precisa de IA?
              não (definição, ler artigo, localizar termo) → Responder → Registrar Consulta      [custo zero]
              sim (alteração, tema) → Montar Pedido → Claude → Formatar Resposta → Preparar Registro
                                      → Responder → Registrar Consulta

O registro e o cache usam as funções de minuta_banco.sql, no mesmo Postgres (Supabase) do MultipleBrains.
"""
import json, os, re, sys, uuid
from collections import deque
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerar_workflow_perguntar as base
from gerar_workflow_perguntar import node, code, http

SAIDA = sys.argv[1] if len(sys.argv) > 1 else '.'
base._pos[0] = 0

N_HOOK, N_CFG, N_MIN, N_GLO, N_VAR = base.N_HOOK, base.N_CFG, base.N_MIN, base.N_GLO, base.N_VAR
N_PED, N_LLM, N_FMT, N_ERRO = base.N_PED, base.N_LLM, base.N_FMT, base.N_ERRO
N_CACHE = 'Buscar no Cache'
N_TEMCACHE = 'Tem cache?'
N_RCACHE = 'Resposta do Cache'
N_CLASS = 'Classificar e Responder'
N_PRECISA = 'Precisa de IA?'
N_PREP = 'Preparar Registro'
N_RESP = 'Responder'
N_REG = 'Registrar Consulta'

# ---------- Configurar: o mesmo do fluxo atual + versão, pergunta normalizada e relógio ----------
JS_CFG = base.JS_CFG.replace(
    "const MODELO = 'claude-haiku-5-5';",
    "const VERSAO = 'Rev.05 r1';   // troque quando ingerir uma minuta nova: o cache é separado por versão\n"
    "const USAR_CACHE = true;\n"
    "const MODELO = 'claude-haiku-5-5';",
).replace(
    "return [{ json: {\n  qdrant_url",
    "const pergunta_norm = pergunta.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase()\n"
    "  .replace(/[“”«»]/g, '\"').replace(/[‘’]/g, \"'\").replace(/\\s+/g, ' ').replace(/\\s*([?!.])+\\s*$/, '').trim();\n\n"
    "return [{ json: {\n  versao: VERSAO, usar_cache: USAR_CACHE, pergunta_norm, t0: Date.now(),\n  qdrant_url",
)
assert 'pergunta_norm' in JS_CFG and 'VERSAO' in JS_CFG

JS_RCACHE = r"""
const cfg = $('%s').first().json;
const c = $input.first().json.cache;
const quando = new Date(c.criado_em).toLocaleString('pt-BR', { timeZone: 'America/Sao_Paulo' });
const saida = Object.assign({}, c.resposta, {
  rota: 'cache', tipo: c.resposta.tipo || null, custo_usd: 0,
  aviso_rota: 'Resposta reaproveitada de uma consulta igual feita em ' + quando + ' (sem custo).',
});
return [{ json: { saida, registro: {
  pergunta: cfg.pergunta, pergunta_norm: cfg.pergunta_norm, versao: cfg.versao, tipo: saida.tipo || 'tema', rota: 'cache',
  resposta: saida, valida: true, custo_usd: 0, duracao_ms: Date.now() - cfg.t0, origem_cache_id: c.id,
} } }];
""" % N_CFG

# ---------- Classificador por regras + respostas por programa (sem IA) ----------
JS_CLASS = r"""
const cfg = $('%s').first().json;
const v = $input.first().json;
const pontos = ($('%s').first().json.result || {}).points || [];
const gloss = (($('%s').first().json.result || {}).points || []).map((p) => p.payload).filter((g) => g && g.termo);
const arts = pontos.map((p) => p.payload).filter((p) => p && p.numero != null).sort((a, b) => a.numero - b.numero);
const porNum = new Map(arts.map((a) => [Number(a.numero), a]));

const semAc = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const L = '[\\p{L}\\p{N}]';
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const reTermo = (t, prefixo) => new RegExp('(?<!' + L + ')' + esc(semAc(t).trim().replace(/\s+/g, ' ')).replace(/ /g, '\\s+') + (prefixo ? L + '*' : '(?!' + L + ')'), 'gu');
function achar(termo, prefixo) {
  const re = reTermo(termo, prefixo); const out = [];
  for (const a of arts) {
    const n = semAc(a.texto_artigo); const m = n.match(re);
    if (m) {
      re.lastIndex = 0; const i = n.search(re);
      const ini = Math.max(0, i - 70), trecho = a.texto_artigo.slice(ini, i + 110).replace(/\s+/g, ' ').trim();
      out.push({ artigo: a.numero, ocorrencias: m.length, trecho: (ini ? '…' : '') + trecho + '…', titulo: (a.breadcrumb || '').split(' > ')[0] });
    }
  }
  return out;
}
const rot = (n) => n < 10 ? 'Art. ' + n + 'º' : 'Art. ' + n;
const q = cfg.pergunta, qn = cfg.pergunta_norm;
const alvos = v.alvos || [];
const aspas = (v.termos || []).map((t) => t.termo);

const ANALISE = /\b(impacto|impacta|impactos|consequen|efeito|afeta|afetaria|por que|porque|analis|explique|explica|resum|compar|diferen|avali|riscos?|o que muda|mudaria|contradi|conflit)/;
const ALTERA = /\b(troc|mud[ae]|mudar|mudan|substitu|alter|modific|suprim|revog|exclu|retir|acrescent|inclu[ia]r|nova redacao|emenda|reescrev)/;
const LOCALIZA = /\b(quais|qual artigo|quantos|quantas|quantidade|lista|listar|liste|onde|em que artigo|em quais|aparece|aparecem|ocorre|ocorrencia|menciona|mencionam|cita|citam|contem|contém|tem|tem o termo|possuem|possui|falam|fala|tratam|trata)\b/;
const DEFINE = /^(o que (e|sao|significa|significam|quer dizer)|qual (e )?(o|a) (definicao|conceito|significado)|defina|definicao de|conceito de|significado de)\b/;
const LER = /\b(o que (diz|dispoe|estabelece|preve|determina|fala)|leia|ler|mostre|mostrar|mostra|texto|transcrev|teor|conteudo|na integra|redacao atual)\b/;

let tipo = 'tema';
if (v.troca || (alvos.length && ALTERA.test(qn))) tipo = 'alteracao';
else if (DEFINE.test(qn)) tipo = 'definicao';
else if (alvos.length && LER.test(qn) && !ANALISE.test(qn)) tipo = 'artigo';
else if (LOCALIZA.test(qn) && !ANALISE.test(qn) && !ALTERA.test(qn)) tipo = 'localizar';

let saida = null;

// ---- localizar: lista exata por programa ----
if (tipo === 'localizar') {
  let termos = aspas.slice(), extraido = false;
  if (!termos.length) {
    const m = q.match(/(?:falam|fala|tratam|trata|mencionam|menciona|citam|cita|cont[eê]m|t[eê]m|possuem|possui|aparece[m]?|o termo|a palavra|a express[aã]o)\s+(?:de|do|da|dos|das|sobre|o|a|os|as|em)?\s*([^?!.;]{2,60})/i);
    if (m) { termos = [m[1].replace(/\s+(na|da|nesta|desta)\s+minuta.*$/i, '').replace(/^(o|a|os|as)\s+/i, '').trim()]; extraido = true; }
  }
  termos = termos.filter((t) => t.length >= 2).slice(0, 4);
  if (!termos.length) tipo = 'tema';
  else {
    const res = termos.map((t) => ({ termo: t, achados: achar(t) }));
    const total = res.reduce((s, r) => s + r.achados.length, 0);
    if (!total && extraido) tipo = 'tema';      // termo deduzido sem aspas e nada achado: deixa a IA interpretar
    else {
      const linhas = res.map((r) => {
        const oc = r.achados.reduce((s, x) => s + x.ocorrencias, 0);
        if (!r.achados.length) {
          // expressão não encontrada: mostra onde aparecem as palavras dela, para orientar uma nova busca
          const PARE = new Set(['para', 'como', 'sobre', 'entre', 'pelos', 'pelas', 'desta', 'deste', 'minuta']);
          const palavras = semAc(r.termo).split(/\s+/).filter((w) => w.length >= 4 && !PARE.has(w));
          const perto = palavras.length > 1 ? palavras.map((w) => { const a = achar(w.replace(/s$/, ''), true); return a.length ? '“' + w + '…” em ' + a.slice(0, 8).map((x) => rot(x.artigo)).join(', ') + (a.length > 8 ? ' e mais ' + (a.length - 8) : '') : null; }).filter(Boolean) : [];
          return '“' + r.termo + '” não aparece em nenhum dos ' + arts.length + ' artigos.' + (perto.length ? ' Palavras parecidas: ' + perto.join('; ') + '.' : '');
        }
        const tit = {}; r.achados.forEach((x) => { tit[x.titulo] = (tit[x.titulo] || 0) + 1; });
        return '“' + r.termo + '” aparece ' + oc + ' vez' + (oc > 1 ? 'es' : '') + ' em ' + r.achados.length + ' artigo' + (r.achados.length > 1 ? 's' : '') +
          '. Por título: ' + Object.entries(tit).map(([t, n]) => (t || 'sem título') .split(' – ')[0] + ' (' + n + ')').join('; ') + '.';
      });
      const todos = [...new Set(res.flatMap((r) => r.achados.map((x) => x.artigo)))].sort((a, b) => a - b);
      saida = {
        resposta: linhas.join('\n') + '\n\nContagem feita por programa nos ' + arts.length + ' artigos (ignora acentos e maiúsculas; palavra inteira).' +
          (extraido ? ' Termo entendido: “' + termos[0] + '”. Para buscar outro termo exato, coloque-o entre aspas.' : ''),
        fontes: todos.map((n) => ({ artigo: n })),
        lista: res.flatMap((r) => r.achados.map((x) => ({ termo: r.termo, artigo: x.artigo, ocorrencias: x.ocorrencias, trecho: x.trecho }))),
        impactos: [],
      };
    }
  }
}

// ---- ler artigo: texto integral ----
if (tipo === 'artigo') {
  const ns = alvos.filter((n) => porNum.has(n)).slice(0, 5);
  saida = {
    resposta: ns.map((n) => { const a = porNum.get(n); return (a.breadcrumb ? '[' + a.breadcrumb + ']\n' : '') + a.texto_artigo; }).join('\n\n'),
    fontes: ns.map((n) => ({ artigo: n })), impactos: [],
  };
}

// ---- definição: glossário (+ onde o termo aparece) ----
if (tipo === 'definicao') {
  let alvo = aspas[0] || qn.replace(DEFINE, '').replace(/^\s*(o termo|a palavra|a expressao|o|a|os|as)\s+/, '').replace(/\s+(na|da|nesta|desta|segundo a)\s+minuta.*$/, '').replace(/[?"']/g, '').trim();
  const g = alvo.length >= 3 && (gloss.find((x) => semAc(x.termo) === semAc(alvo)) || gloss.find((x) => semAc(x.termo).replace(/\s*\(.*\)$/, '') === semAc(alvo))
    || gloss.find((x) => semAc(x.termo).includes(semAc(alvo)) || semAc(x.termo).includes('(' + semAc(alvo) + ')')));
  if (!g) tipo = 'tema';
  else {
    const base = String(g.termo).replace(/\s*\(.*?\)\s*$/, '');
    const ach = achar(base);
    saida = {
      resposta: g.termo + ': ' + g.definicao + '\n\n(Definição do glossário da minuta.)' +
        (ach.length ? '\n\nO termo aparece em ' + ach.length + ' artigo' + (ach.length > 1 ? 's' : '') + ': ' + ach.slice(0, 40).map((x) => rot(x.artigo)).join(', ') + (ach.length > 40 ? '…' : '') + '.' : ''),
      fontes: ach.slice(0, 40).map((x) => ({ artigo: x.artigo })), impactos: [],
    };
  }
}

const precisa_ia = !saida;
const out = { ...v, tipo, precisa_ia };
if (saida) {
  saida = Object.assign(saida, { pergunta: q, tipo, rota: 'programa', custo_usd: 0,
    aviso_rota: 'Respondido por programa, sem IA (custo zero).',
    varredura: { total_artigos_percorridos: arts.length, termos: [], checklist: [], nao_avaliados_pela_ia: [] } });
  out.saida = saida;
  out.registro = { pergunta: q, pergunta_norm: cfg.pergunta_norm, versao: cfg.versao, tipo, rota: 'programa', resposta: saida,
    valida: true, custo_usd: 0, duracao_ms: Date.now() - cfg.t0 };
}
// textos grandes não precisam seguir adiante quando a resposta já está pronta
if (saida) { delete out.minuta_texto; delete out.glossario_texto; }
return [{ json: out }];
""" % (N_CFG, N_MIN, N_GLO)

JS_PREP = r"""
const cfg = $('%s').first().json;
const cl = $('%s').first().json;
const s = $input.first().json;
// preço por milhão de tokens (Anthropic). Haiku 5.5 muda de faixa acima de 100 mil tokens de entrada.
const u = s.uso || {};
const ent = u.input_tokens || 0, grava = u.cache_creation_input_tokens || 0, le = u.cache_read_input_tokens || 0, sai = u.output_tokens || 0;
const total = ent + grava + le;
let p = total > 100000 ? { i: 0.5, w: 1, r: 0.05, o: 2.5 } : { i: 0.10, w: 0.20, r: 0.01, o: 0.50 };
if (/sonnet/.test(s.modelo || '')) p = { i: 2, w: 4, r: 0.10, o: 10 };
const custo = Math.round(((ent * p.i + grava * p.w + le * p.r + sai * p.o) / 1e6) * 1e6) / 1e6;
const valida = !s.parada || s.parada === 'end_turn';
const saida = Object.assign({}, s, { tipo: cl.tipo, rota: 'ia', custo_usd: custo,
  aviso_rota: 'Analisado por IA (' + (s.modelo || 'modelo') + ') a partir da varredura dos ' + (s.varredura ? s.varredura.total_artigos_percorridos : '') + ' artigos.' });
return [{ json: { saida, registro: {
  pergunta: cfg.pergunta, pergunta_norm: cfg.pergunta_norm, versao: cfg.versao, tipo: cl.tipo, rota: 'ia', resposta: saida, valida,
  modelo: s.modelo, tokens_entrada: total, tokens_saida: sai, custo_usd: custo, duracao_ms: Date.now() - cfg.t0,
} } }];
""" % (N_CFG, N_CLASS)


def postgres(name, query, params_expr, notes):
    n = node(name, "n8n-nodes-base.postgres", 2.5, {
        "operation": "executeQuery", "query": query,
        "options": {"queryReplacement": params_expr},
    }, notes=notes, notesInFlow=True)
    n["onError"] = "continueRegularOutput"   # banco fora do ar não impede a resposta
    n["alwaysOutputData"] = True
    return n


def se(name, expr):
    return node(name, "n8n-nodes-base.if", 2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
            "conditions": [{"id": str(uuid.uuid4()), "leftValue": expr, "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
            "combinator": "and",
        },
        "options": {},
    })


def build():
    CFG = f"$('{N_CFG}').first().json"
    nodes = [
        node(N_HOOK, "n8n-nodes-base.webhook", 2, {
            "httpMethod": "POST", "path": "minuta-perguntar-orq", "responseMode": "responseNode",
            "options": {"allowedOrigins": "*"},
        }, webhookId=str(uuid.uuid4())),
        code(N_CFG, JS_CFG, notes="QDRANT_URL, VERSAO, modelo e modo"),
        postgres(N_CACHE, "select fn_minuta_cache_buscar($1, $2) as cache",
                 "={{ [$json.usar_cache ? $json.pergunta_norm : '__sem_cache__', $json.versao] }}",
                 "Credencial: Postgres do MultipleBrains (Supabase)"),
        se(N_TEMCACHE, "={{ !!($json.cache && $json.cache.resposta) }}"),
        code(N_RCACHE, JS_RCACHE),
        http(N_MIN, f"={{{{ {CFG}.qdrant_url }}}}/collections/{{{{ {CFG}.colecao_minuta }}}}/points/scroll",
             '={{ JSON.stringify({ limit: 2000, with_payload: ["numero", "codigo", "breadcrumb", "texto_artigo"], with_vector: false }) }}',
             notes="Credencial: Qdrant"),
        http(N_GLO, f"={{{{ {CFG}.qdrant_url }}}}/collections/{{{{ {CFG}.colecao_glossario }}}}/points/scroll",
             '={{ JSON.stringify({ limit: 500, with_payload: ["termo", "definicao"], with_vector: false }) }}',
             notes="Credencial: Qdrant"),
        code(N_VAR, base.JS_VAR, notes="Varre todos os artigos, sem IA"),
        code(N_CLASS, JS_CLASS, notes="Decide o tipo da pergunta; responde sem IA quando dá"),
        se(N_PRECISA, "={{ $json.precisa_ia }}"),
        code(N_PED, base.JS_PED),
        http(N_LLM, "https://api.anthropic.com/v1/messages", "={{ JSON.stringify($json.body) }}",
             notes="Credencial: Anthropic (Header Auth x-api-key)", headers={"anthropic-version": "2023-06-01"}, timeout=180000),
        code(N_FMT, base.JS_FMT),
        code(N_PREP, JS_PREP),
        node(N_RESP, "n8n-nodes-base.respondToWebhook", 1.1, {
            "respondWith": "json", "responseBody": "={{ JSON.stringify($json.saida) }}",
            "options": {"responseHeaders": {"entries": [{"name": "Access-Control-Allow-Origin", "value": "*"}]}},
        }),
        postgres(N_REG, "select fn_minuta_registrar($1::jsonb) as id", "={{ [JSON.stringify($json.registro)] }}",
                 "Credencial: Postgres do MultipleBrains (Supabase)"),
    ]
    erro = node(N_ERRO, "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "json",
        "responseBody": "={{ JSON.stringify({ erro: (($json.error && ($json.error.message || $json.error)) || 'Falha no fluxo'), no: $prevNode.name, detalhe: ($json.error && $json.error.description) || null }) }}",
        "options": {"responseHeaders": {"entries": [{"name": "Access-Control-Allow-Origin", "value": "*"}]}},
    })
    nodes.append(erro)

    # posições em duas faixas para ficar legível no editor
    pos = {N_HOOK: (0, 300), N_CFG: (220, 300), N_CACHE: (440, 300), N_TEMCACHE: (660, 300),
           N_RCACHE: (880, 80), N_MIN: (880, 300), N_GLO: (1100, 300), N_VAR: (1320, 300), N_CLASS: (1540, 300),
           N_PRECISA: (1760, 300), N_PED: (1980, 420), N_LLM: (2200, 420), N_FMT: (2420, 420), N_PREP: (2640, 420),
           N_RESP: (2860, 300), N_REG: (3080, 300), N_ERRO: (1760, 700)}
    for n in nodes:
        if n["name"] in pos:
            n["position"] = list(pos[n["name"]])

    def liga(a, b, saida=0):
        conns.setdefault(a, {"main": []})
        while len(conns[a]["main"]) <= saida:
            conns[a]["main"].append([])
        conns[a]["main"][saida].append({"node": b, "type": "main", "index": 0})

    conns = {}
    liga(N_HOOK, N_CFG); liga(N_CFG, N_CACHE); liga(N_CACHE, N_TEMCACHE)
    liga(N_TEMCACHE, N_RCACHE, 0); liga(N_TEMCACHE, N_MIN, 1)
    liga(N_RCACHE, N_RESP)
    liga(N_MIN, N_GLO); liga(N_GLO, N_VAR); liga(N_VAR, N_CLASS); liga(N_CLASS, N_PRECISA)
    liga(N_PRECISA, N_PED, 0); liga(N_PRECISA, N_RESP, 1)
    liga(N_PED, N_LLM); liga(N_LLM, N_FMT); liga(N_FMT, N_PREP); liga(N_PREP, N_RESP)
    liga(N_RESP, N_REG)
    # erros: nós de código e HTTP desviam para "Responder Erro"
    for n in nodes:
        if n["type"] in ("n8n-nodes-base.code", "n8n-nodes-base.httpRequest"):
            n["onError"] = "continueErrorOutput"
            liga(n["name"], N_ERRO, 1)

    nodes.append({"parameters": {"content": f"""## Pergunte usando I.A. — orquestrador

Classifica a pergunta e só chama a IA quando precisa:
- **Definição** (glossário), **ler artigo** e **localizar/contar termo** → respondidos por programa, **custo zero**.
- **Alteração/impacto** e **tema aberto** → varredura completa + Claude Haiku (modo econômico).
- Pergunta **igual** a uma já respondida pela IA (mesma versão da minuta) → devolve a resposta guardada, **custo zero**.

**Antes de ativar**
1. Rode `minuta_banco.sql` no Supabase do MultipleBrains (SQL Editor). Só cria tabelas novas `minuta_*`.
2. Credencial **Postgres** (a mesma do MultipleBrains) em **{N_CACHE}** e **{N_REG}**.
3. Credencial **Qdrant** em **{N_MIN}** e **{N_GLO}**; **Anthropic** em **{N_LLM}**.
4. Webhook: **Respond = Using 'Respond to Webhook' Node**. Endereço próprio (`/webhook/minuta-perguntar-orq`): pode ficar ativo junto com os outros fluxos.

Custos por dia: `select * from vw_minuta_custo_diario;`
""", "height": 520, "width": 640}, "id": str(uuid.uuid4()), "name": "Leia-me",
                  "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [200, -320]})
    return {"name": "Minuta PD — Pergunte (orquestrador)", "nodes": nodes, "connections": conns,
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
    caminho = os.path.join(SAIDA, 'minuta_n8n_orquestrador.json')
    json.dump(wf, open(caminho, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'{caminho}: {n} nós, conexões e referências válidas')
