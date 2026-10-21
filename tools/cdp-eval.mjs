import { WebSocket } from 'ws';

const [, , portArg, ...exprParts] = process.argv;
const port = Number(portArg) || 9922;
const expr = exprParts.join(' ');
const verbose = process.env.CDP_VERBOSE === '1';

const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
if (verbose) console.error('targets:', list.map(t => `${t.type}:${t.title}`));
const target = list.find(t => t.type === 'page' && /chatgpt\.com/.test(t.url || ''))
  || list.find(t => t.type === 'page' && (t.url || '') !== 'about:blank')
  || list.find(t => t.type === 'page');
if (!target) { console.error('no page target'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl, { maxPayload: 512 * 1024 * 1024 });
let nid = 0;
const pending = new Map();

function send(method, params = {}) {
  return new Promise((resolve) => {
    const id = ++nid;
    pending.set(id, resolve);
    if (verbose) console.error('>>', method);
    ws.send(JSON.stringify({ id, method, params }));
  });
}

ws.on('message', (raw) => {
  const m = JSON.parse(raw.toString());
  if (verbose && !m.id) console.error('<<', m.method);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});

await new Promise((r, e) => { ws.on('open', r); ws.on('error', e); });

await send('Runtime.enable');
const res = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });

ws.close();
if (res.error) { console.error('cdp error:', JSON.stringify(res.error)); process.exit(2); }
const v = res.result?.result;
if (v?.objectId) console.log(JSON.stringify({ objectId: v.objectId, type: v.type }));
else console.log(JSON.stringify(v?.value ?? v?.description ?? v, null, 1));
process.exit(0);
