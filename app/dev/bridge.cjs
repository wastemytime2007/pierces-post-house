// Development only: connects the browser harness (harness.html?bridge=1) to the REAL Python backend, so screens can be checked against real events and real projects.
//   node dev/bridge.cjs [port]        starts the backend and listens on 127.0.0.1:<port> (default 47950)
//   POST /cmd      body: one command as JSON  -> written to the backend's stdin
//   GET  /events   server-sent events, one per line the backend prints
// It only relays. Which commands a test sends is up to the test: the e2e scripts send reads (list_projects, project_posters, list_sequences, frame_thumbs).
const http = require("http");
const { spawn } = require("child_process");
const path = require("path");

const port = parseInt(process.argv[2] || "47950", 10);
const backend = path.join(__dirname, "..", "python_backend", "backend.py");
const py = spawn(process.env.PYTHON || "python3", [backend], { cwd: path.dirname(backend), stdio: ["pipe", "pipe", "inherit"] });
const clients = new Set();
let buf = "";
const log = [];
py.stdout.on("data", (d) => {
  buf += d.toString("utf8");
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i);
    buf = buf.slice(i + 1);
    if (!line.trim()) continue;
    log.push(line);
    if (log.length > 400) log.shift();
    for (const c of clients) c.write(`data: ${line}\n\n`);
  }
});
py.on("exit", () => process.exit(0));

const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
http.createServer((req, res) => {
  if (req.method === "OPTIONS") { res.writeHead(204, cors); return res.end(); }
  if (req.url === "/events") {
    res.writeHead(200, { ...cors, "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    for (const l of log) res.write(`data: ${l}\n\n`);                    // a page that connects late still hears the startup "ready"
    clients.add(res);
    req.on("close", () => clients.delete(res));
    return;
  }
  if (req.url === "/cmd" && req.method === "POST") {
    let body = "";
    req.on("data", (d) => (body += d));
    req.on("end", () => { try { JSON.parse(body); py.stdin.write(body.replace(/\n/g, " ") + "\n"); res.writeHead(204, cors); } catch (e) { res.writeHead(400, cors); } res.end(); });
    return;
  }
  res.writeHead(404, cors);
  res.end();
}).listen(port, "127.0.0.1", () => console.log(`bridge on ${port}, backend pid ${py.pid}`));

process.on("SIGTERM", () => { py.kill(); process.exit(0); });
process.on("SIGINT", () => { py.kill(); process.exit(0); });
