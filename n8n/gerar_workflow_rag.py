"""Gera o workflow n8n "Minuta PD — Pergunte (RAG simples)" para comparar com a varredura completa.

Fluxo: Webhook POST /webhook/minuta-perguntar-rag → Configurar → Embedding da pergunta (OpenAI
text-embedding-3-small, o mesmo modelo do notebook de ingestão) → busca dos K artigos mais parecidos
no Qdrant (alias minuta_ultima_versao) → Claude Haiku → resposta no mesmo formato do outro fluxo.
"""
import json, os, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gerar_workflow_perguntar as base
from gerar_workflow_perguntar import node, code, http, QDRANT_URL

SAIDA = sys.argv[1] if len(sys.argv) > 1 else '.'
base._pos[0] = 0

N_HOOK = 'Webhook Pergunta RAG'
N_CFG = 'Configurar e Validar'
N_EMB = 'Embedding da Pergunta'
N_BUSCA = 'Buscar Artigos Parecidos'
N_PED = 'Montar Pedido'
N_LLM = 'Claude'
N_FMT = 'Formatar Resposta'
N_RESP = 'Responder'
N_ERRO = 'Responder Erro'

JS_CFG = r"""
// ==== CONFIGURE AQUI ====
const QDRANT_URL = '%s';
const COLECAO_MINUTA = 'minuta_ultima_versao';   // alias do notebook de ingestão
const MODELO = 'claude-haiku-5-5';
const TOP_K = 8;                                 // artigos enviados à IA
const MAX_TOKENS_RESPOSTA = 4000;
// ========================
const corpo = $input.first().json.body || {};
const pergunta = String(corpo.pergunta || '').trim();
if (pergunta.length < 4) throw new Error('Pergunta vazia ou curta demais.');
if (pergunta.length > 2000) throw new Error('Pergunta longa demais (máx. 2000 caracteres).');
return [{ json: { qdrant_url: QDRANT_URL.replace(/\/+$/, ''), colecao: COLECAO_MINUTA, modelo: MODELO,
  top_k: TOP_K, max_tokens: MAX_TOKENS_RESPOSTA, pergunta } }];
""" % QDRANT_URL

JS_PED = r"""
const cfg = $('%s').first().json;
const pontos = ($input.first().json.result || []);
const lista = (Array.isArray(pontos) ? pontos : (pontos.points || [])).map((p) => ({ ...p.payload, score: p.score }))
  .filter((p) => p.numero != null).sort((a, b) => a.numero - b.numero);
const contexto = lista.map((a) => (a.breadcrumb ? '[' + a.breadcrumb + ']\n' : '') + a.texto_artigo).join('\n\n');
const instrucoes = [
  'Você apoia a Comissão da Câmara Municipal de Curitiba na análise do Projeto de Lei de revisão do Plano Diretor.',
  'Você recebe alguns artigos da minuta escolhidos por semelhança com a pergunta (não a minuta inteira).',
  'Responda em português, com base SOMENTE nesses artigos, citando "Art. N". Se eles não bastarem, diga isso.',
  'Não emita opinião sobre o mérito político; descreva o que o texto diz.',
  'Responda APENAS com JSON válido: {"resposta": "texto", "artigos": [números usados]}.',
].join('\n');
return [{ json: {
  recuperados: lista.map((a) => ({ artigo: a.numero, score: Math.round(a.score * 1000) / 1000 })),
  body: { model: cfg.modelo, max_tokens: cfg.max_tokens,
    system: [{ type: 'text', text: instrucoes }],
    messages: [{ role: 'user', content: 'ARTIGOS:\n\n' + (contexto || '(nenhum)') + '\n\nPERGUNTA: ' + cfg.pergunta }] },
} }];
""" % N_CFG

JS_FMT = r"""
const cfg = $('%s').first().json;
const ped = $('%s').first().json;
const emb = $('%s').first().json;
const r = $input.first().json;
const bruto = (r.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('');
let saida;
try { saida = JSON.parse(bruto.slice(bruto.indexOf('{'), bruto.lastIndexOf('}') + 1)); }
catch (e) { saida = { resposta: bruto, artigos: [] }; }
const disponiveis = new Set(ped.recuperados.map((x) => x.artigo));
const usados = [...new Set((saida.artigos || []).map(Number))].filter((n) => disponiveis.has(n)).sort((a, b) => a - b);
return [{ json: {
  pergunta: cfg.pergunta,
  resposta: String(saida.resposta || '').trim() || 'Não consegui formular uma resposta.',
  fontes: usados.map((n) => ({ artigo: n })),
  impactos: [],
  recuperados: ped.recuperados,
  modo: 'rag',
  parada: r.stop_reason || null,
  uso: r.usage || null,
  modelo: r.model || null,
  uso_embedding: emb.usage || null,
} }];
""" % (N_CFG, N_PED, N_EMB)

CFG = f"$('{N_CFG}').first().json"


def build():
    nodes = [
        node(N_HOOK, "n8n-nodes-base.webhook", 2, {
            "httpMethod": "POST", "path": "minuta-perguntar-rag", "responseMode": "responseNode",
            "options": {"allowedOrigins": "*"},
        }, webhookId=str(uuid.uuid4())),
        code(N_CFG, JS_CFG, notes="QDRANT_URL, alias, modelo e TOP_K"),
        http(N_EMB, "https://api.openai.com/v1/embeddings",
             f'={{{{ JSON.stringify({{ model: "text-embedding-3-small", input: {CFG}.pergunta }}) }}}}',
             notes="Credencial: OpenAI (mesmo modelo do notebook)"),
        http(N_BUSCA, f"={{{{ {CFG}.qdrant_url }}}}/collections/{{{{ {CFG}.colecao }}}}/points/search",
             f'={{{{ JSON.stringify({{ vector: $json.data[0].embedding, limit: {CFG}.top_k, with_payload: ["numero", "breadcrumb", "texto_artigo"] }}) }}}}',
             notes="Credencial: Qdrant"),
        code(N_PED, JS_PED),
        http(N_LLM, "https://api.anthropic.com/v1/messages", "={{ JSON.stringify($json.body) }}",
             notes="Credencial: Anthropic (Header Auth x-api-key)", headers={"anthropic-version": "2023-06-01"}, timeout=120000),
        code(N_FMT, JS_FMT),
        node(N_RESP, "n8n-nodes-base.respondToWebhook", 1.1, {
            "respondWith": "json", "responseBody": "={{ JSON.stringify($json) }}",
            "options": {"responseHeaders": {"entries": [{"name": "Access-Control-Allow-Origin", "value": "*"}]}},
        }),
    ]
    erro = node(N_ERRO, "n8n-nodes-base.respondToWebhook", 1.1, {
        "respondWith": "json",
        "responseBody": "={{ JSON.stringify({ erro: (($json.error && ($json.error.message || $json.error)) || 'Falha no fluxo'), no: $prevNode.name, detalhe: ($json.error && $json.error.description) || null }) }}",
        "options": {"responseHeaders": {"entries": [{"name": "Access-Control-Allow-Origin", "value": "*"}]}},
    })
    erro["position"] = [1150, 560]
    nodes.append(erro)
    nodes.append({"parameters": {"content": f"""## Pergunte à Minuta — RAG simples (para comparação)

Busca os 8 artigos mais parecidos com a pergunta e manda só eles ao Claude Haiku. Não faz varredura completa.

**Antes de ativar**
1. Webhook: confira **Respond = Using 'Respond to Webhook' Node**.
2. Credenciais: **OpenAI** em **{N_EMB}** (Header Auth `Authorization: Bearer ...`), **Qdrant** em **{N_BUSCA}**, **Anthropic** em **{N_LLM}**.
3. Ative. URL: `https://n8n.planodiretorcuritiba.cloud/webhook/minuta-perguntar-rag`.
""", "height": 340, "width": 560}, "id": str(uuid.uuid4()), "name": "Leia-me",
                  "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [220, -160]})
    ordem = [N_HOOK, N_CFG, N_EMB, N_BUSCA, N_PED, N_LLM, N_FMT, N_RESP]
    conns = {}
    for a, b in zip(ordem, ordem[1:]):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
        if a != N_HOOK:
            conns[a]["main"].append([{"node": N_ERRO, "type": "main", "index": 0}])
    for n in nodes:
        if n["name"] in ordem[1:-1]:
            n["onError"] = "continueErrorOutput"
    return {"name": "Minuta PD — Pergunte (RAG simples)", "nodes": nodes, "connections": conns,
            "active": False, "settings": {"executionOrder": "v1"}, "pinData": {}}


if __name__ == '__main__':
    wf = build()
    base.N_HOOK = N_HOOK  # validar() parte do webhook
    n = base.validar(wf)
    caminho = os.path.join(SAIDA, 'minuta_n8n_perguntar_rag.json')
    json.dump(wf, open(caminho, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'{caminho}: {n} nós, conexões e referências válidas')
