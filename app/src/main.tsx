import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import type { FormEvent } from "react";
import "./styles.css";

console.log("[frontier] UI bundle loaded", {
  path: window.location.pathname,
  timestamp: new Date().toISOString(),
});

type View = "overview" | "services" | "discovery" | "mimic" | "compare";
type Service = { id: string; name: string; base_url: string; status: string; request_interval_ms: number };
type DiscoveryRun = { id: string; service_id: string; status: string; started_at?: string | null; finished_at?: string | null; error?: string | null };
type RunEvent = { id: string; step: string; message: string; level: string; created_at?: string | null; progress_done?: number | null; progress_total?: number | null; payload?: string | null; response?: string | null; response_code?: number | null };
type Insight = { id: string; title: string; content: string; confidence: number; status: string };
type MimicRun = { id: string; service_id: string; status: string; iterations: number; match_rate?: number | null; code_ref?: string | null; error?: string | null };
type EndpointStat = { method: string; path: string; purpose?: string; matched: number; total: number };
type MimicIteration = { iteration: number; match_rate: number; matched: number; total: number; use_case_count: number; code_ref?: string | null; endpoints?: EndpointStat[] | null; validated?: boolean | null; selected?: boolean };
type Clone = { id: string; service_id: string; slug: string; enabled: boolean; token?: string };
type SeedRequest = { method: string; path: string; payload_ref?: string | null; response_code?: number | null; response_ref?: string | null; sequence_order: number };
const API_URL = import.meta.env.VITE_API_URL ?? "";

function parseCsv(text: string): SeedRequest[] {
  const rows: string[][] = [];
  let row: string[] = [], cell = "", quoted = false;
  const delimiter = (text.split(/\r?\n/, 1)[0] ?? "").includes("|") ? "|" : ",";
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === '"' && text[i + 1] === '"' && quoted) { cell += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === delimiter && !quoted) { row.push(cell.trim()); cell = ""; }
    else if ((char === "\n" || char === "\r") && !quoted) { if (char === "\r" && text[i + 1] === "\n") i += 1; row.push(cell.trim()); if (row.some(Boolean)) rows.push(row); row = []; cell = ""; }
    else cell += char;
  }
  if (cell || row.length) { row.push(cell.trim()); rows.push(row); }
  if (rows.length < 2) return [];
  const headers = rows[0].map(header => header.toLowerCase().trim());
  const indexOf = (...names: string[]) => names.map(name => headers.indexOf(name)).find(index => index >= 0) ?? -1;
  const methodIndex = indexOf("verbo", "method", "http_method");
  const pathIndex = indexOf("path", "ruta", "url");
  const payloadIndex = indexOf("payload", "body", "request_content");
  const codeIndex = indexOf("response_code", "status", "codigo");
  const responseIndex = indexOf("response_content", "response", "expected_response");
  if (methodIndex < 0 || pathIndex < 0) throw new Error("El CSV debe tener las columnas verbo/method y path/ruta");
  return rows.slice(1).filter(values => values[pathIndex]).map((values, sequence_order) => {
    const payload = payloadIndex >= 0 ? values[payloadIndex] : "";
    const parsedCode = codeIndex >= 0 && values[codeIndex] ? Number(values[codeIndex]) : NaN;
    return { method: values[methodIndex].toUpperCase(), path: values[pathIndex], payload_ref: payload ? `inline:${payload}` : null, response_code: Number.isFinite(parsedCode) ? parsedCode : null, response_ref: responseIndex >= 0 && values[responseIndex] ? `inline:${values[responseIndex]}` : null, sequence_order };
  });
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", headers: { "Content-Type": "application/json", "Cache-Control": "no-cache" }, ...options });
  if (!res.ok) throw new Error(await res.text());
  return res.status === 204 ? (undefined as T) : res.json();
}

function App() {
  const [view, setView] = useState<View>("overview");
  const [services, setServices] = useState<Service[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ name: "", base_url: "", request_interval_ms: 250, authorization_token: "" });
  const [editingServiceId, setEditingServiceId] = useState<string | null>(null);
  const loadServices = () => api<Service[]>("/api/v1/services").then(setServices).catch(() => setError("La API aún no está conectada."));
  useEffect(() => { void loadServices(); }, []);
  async function createService(event: FormEvent) {
    event.preventDefault(); setError("");
    try {
      const service = await api<Service>(editingServiceId ? `/api/v1/services/${editingServiceId}` : "/api/v1/services", { method: editingServiceId ? "PATCH" : "POST", body: JSON.stringify(form) });
      if (!editingServiceId && form.authorization_token.trim()) await api(`/api/v1/services/${service.id}/credentials`, { method: "POST", body: JSON.stringify({ data: { kind: "bearer", placement: "header", param_name: "Authorization", value: form.authorization_token.trim() } }) });
      setForm({ name: "", base_url: "", request_interval_ms: 250, authorization_token: "" }); setEditingServiceId(null); await loadServices(); setView("services");
    }
    catch { setError("No fue posible registrar el servicio."); }
  }
  function editService(service: Service) { setEditingServiceId(service.id); setForm({ name: service.name, base_url: service.base_url, request_interval_ms: service.request_interval_ms, authorization_token: "" }); setView("services"); setError(""); }
  async function deleteService(service: Service) { if (!window.confirm(`¿Borrar ${service.name}? Esta acción no se puede deshacer.`)) return; try { await api(`/api/v1/services/${service.id}`, { method: "DELETE" }); if (editingServiceId === service.id) { setEditingServiceId(null); setForm({ name: "", base_url: "", request_interval_ms: 250, authorization_token: "" }); } await loadServices(); } catch { setError("No fue posible borrar el servicio."); } }
  const navigation: [View, string, string][] = [["overview", "Overview", "⌂"], ["services", "Servicios", "◈"], ["discovery", "Discovery", "◎"], ["mimic", "Mimic & replay", "◇"], ["compare", "Comparador", "⇄"]];
  return <div className="layout"><aside className="sidebar"><div className="brand"><img className="brand-mascot" src="/app/assets/frontier-mimic-mascot.png" alt="Frontier Mimic" /><span>Frontier Mimic</span></div><nav>{navigation.map(([key, label, icon]) => <button key={key} className={view === key ? "nav active" : "nav"} onClick={() => setView(key)}><span>{icon}</span>{label}</button>)}</nav><div className="sidebar-foot"><span className="dot" /> Sistema operativo<br /><small>Railway · development</small></div></aside><main className="main"><header><div><span className="section-label">PLATAFORMA</span><h1>{navigation.find(([key]) => key === view)?.[1]}</h1></div><div className="user-chip">DR <span>David Rojas</span></div></header>{error && <div className="notice">{error}</div>}{view === "overview" && <Overview services={services} onAction={() => setView("services")} />}{view === "services" && <Services services={services} form={form} setForm={setForm} onSubmit={createService} editing={Boolean(editingServiceId)} onEdit={editService} onDelete={deleteService} />}{view === "discovery" && <Discovery services={services} />}{view === "mimic" && <Mimic services={services} />}{view === "compare" && <Compare />}</main></div>;
}

function Overview({ services, onAction }: { services: Service[]; onAction: () => void }) { return <><div className="hero"><div><span className="pill">BLACK BOX INTELLIGENCE</span><h2>Entiende lo que no puedes ver.</h2><p>Descubre el comportamiento de un servicio, genera su reemplazo y valida la equivalencia.</p><button onClick={onAction}>Registrar servicio <span>→</span></button></div><div className="hero-art"><i /><i /><i /><i /></div></div><div className="metric-grid"><Metric label="Servicios registrados" value={services.length} hint="en tu workspace" /><Metric label="Discoveries activos" value="—" hint="listos para ejecutar" /><Metric label="Clones habilitados" value="—" hint="esperando ship" /></div><div className="panel"><div className="panel-head"><div><span className="section-label">FLUJO PRINCIPAL</span><h3>Del descubrimiento al ship</h3></div><span className="muted">4 etapas</span></div><div className="flow">{["Configuración", "Discovery", "Mimic", "Ship"].map((x, i) => <div className="flow-step" key={x}><span>{String(i + 1).padStart(2, "0")}</span><strong>{x}</strong><small>{["Define el servicio", "Captura evidencia", "Genera el clon", "Habilita el acceso"][i]}</small></div>)}</div></div></>; }
function Services({ services, form, setForm, onSubmit, editing, onEdit, onDelete }: { services: Service[]; form: { name: string; base_url: string; request_interval_ms: number; authorization_token: string }; setForm: (v: typeof form) => void; onSubmit: (e: FormEvent) => void; editing: boolean; onEdit: (service: Service) => void; onDelete: (service: Service) => void }) { return <div className="columns"><section className="panel"><div className="panel-head"><div><span className="section-label">UC-01</span><h3>{editing ? "Editar caja negra" : "Registrar caja negra"}</h3></div></div><form onSubmit={onSubmit} className="form"><label>Nombre del servicio<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Banco legacy" /></label><label>URL base<input required type="url" value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} placeholder="https://api.example.com" /></label>{!editing && <label>Token Bearer<input type="password" value={form.authorization_token} onChange={e => setForm({ ...form, authorization_token: e.target.value })} placeholder="Token del servicio (opcional)" /></label>}<label>Intervalo entre requests (ms)<input type="number" min="0" value={form.request_interval_ms} onChange={e => setForm({ ...form, request_interval_ms: Number(e.target.value) })} /></label><button type="submit">{editing ? "Actualizar servicio" : "Guardar servicio"} <span>→</span></button></form></section><section className="panel"><div className="panel-head"><div><span className="section-label">INVENTARIO</span><h3>Servicios registrados</h3></div><span className="count">{services.length}</span></div>{services.length === 0 ? <Empty text="Todavía no hay servicios registrados." /> : <div className="service-list">{services.map(service => <div className="service-row" key={service.id}><span className="service-icon">◈</span><div><strong>{service.name}</strong><small>{service.base_url}</small></div><span className="status"><i />{service.status}</span><div className="service-actions"><button type="button" onClick={() => onEdit(service)}>Editar</button><button type="button" className="danger" onClick={() => void onDelete(service)}>Borrar</button></div></div>)}</div>}</section></div>; }
function Discovery({ services }: { services: Service[] }) {
  const [serviceId, setServiceId] = useState(services[0]?.id ?? "");
  const [run, setRun] = useState<DiscoveryRun | null>(null);
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [busy, setBusy] = useState(false);
  const [seedRows, setSeedRows] = useState<SeedRequest[]>([]);
  const [csvName, setCsvName] = useState("");
  const [csvError, setCsvError] = useState("");
  useEffect(() => { if (!serviceId && services[0]) setServiceId(services[0].id); }, [services, serviceId]);
  async function refresh(runId: string, selectedServiceId: string) {
    const [nextRun, nextEvents, nextInsights] = await Promise.all([api<DiscoveryRun>(`/api/v1/discovery-runs/${runId}`), api<RunEvent[]>(`/api/v1/run-events?discovery_run_id=${runId}`), api<Insight[]>(`/api/v1/insights?service_id=${selectedServiceId}`)]);
    setRun(nextRun); setEvents(nextEvents); setInsights(nextInsights); setRuns(previous => previous.map(item => item.id === nextRun.id ? nextRun : item)); return nextRun;
  }
  async function loadRunHistory(selectedServiceId: string) {
    const history = await api<DiscoveryRun[]>(`/api/v1/discovery-runs?service_id=${selectedServiceId}`);
    const ordered = [...history].sort((left, right) => String(right.started_at ?? right.id).localeCompare(String(left.started_at ?? left.id)));
    setRuns(ordered);
    if (ordered.length && (!run || run.service_id !== selectedServiceId)) await refresh(ordered[0].id, selectedServiceId);
    if (!ordered.length) { setRun(null); setEvents([]); setInsights([]); }
  }
  useEffect(() => { if (serviceId) void loadRunHistory(serviceId); }, [serviceId]);
  useEffect(() => {
    if (!run || ["completed", "failed", "cancelled"].includes(run.status)) return;
    const timer = window.setInterval(() => { void refresh(run.id, run.service_id); }, 2500);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status, run?.service_id]);
  async function startDiscovery() {
    if (!serviceId) return;
    setBusy(true); setEvents([]); setInsights([]);
    try {
      const endpoint = seedRows.length ? `/api/v1/services/${serviceId}/discovery-runs/import` : `/api/v1/services/${serviceId}/discovery-runs`;
      const data = seedRows.length ? { mode: "react", requests: seedRows } : { mode: "react" };
      const created = await api<DiscoveryRun>(endpoint, { method: "POST", body: JSON.stringify({ data }) });
      setRun(created); setRuns(previous => [created, ...previous]); await refresh(created.id, serviceId);
    }
    catch { setRun(null); }
    finally { setBusy(false); }
  }
  const lastEvent = events[events.length - 1];
  const visibleEvents = events;
  return <section className="panel"><div className="panel-head"><div><span className="section-label">UC-02 / UC-04</span><h3>Discovery runs</h3></div><div className="discovery-actions"><select disabled={!services.length || busy} value={serviceId} onChange={e => setServiceId(e.target.value)}>{services.map(service => <option key={service.id} value={service.id}>{service.name}</option>)}</select>{runs.length > 0 && <select disabled={busy} value={run?.id ?? ""} onChange={async e => { const selected = runs.find(item => item.id === e.target.value); if (selected) await refresh(selected.id, serviceId); }}><option value="">Seleccionar run</option>{runs.map(item => <option key={item.id} value={item.id}>{item.status} · {item.id.slice(0, 8)}</option>)}</select>}<button disabled={!services.length || busy || run?.status === "running"} onClick={() => void startDiscovery()}>{busy ? "Iniciando…" : "Iniciar discovery"}</button></div></div>{!services.length ? <Empty text="Registra un servicio antes de iniciar discovery." /> : <><div className="csv-loader"><div><strong>Cargar ejemplos CSV</strong><small>Columnas: verbo, path, payload, response_code, response_content</small></div><label className="file-button">{csvName || "Seleccionar CSV"}<input type="file" accept=".csv,text/csv" onChange={async e => { const file = e.target.files?.[0]; if (!file) return; setCsvName(file.name); try { setSeedRows(parseCsv(await file.text())); setCsvError(""); } catch (error) { setSeedRows([]); setCsvError(error instanceof Error ? error.message : "CSV inválido"); } }} /></label>{seedRows.length > 0 && <span className="csv-count">{seedRows.length} ejemplos listos</span>}{csvError && <span className="csv-error">{csvError}</span>}</div><div className="discovery-status"><strong>{run ? run.status : "ready"}</strong><span>{lastEvent?.message ?? "El agente ReAct está listo para explorar la caja negra."}</span></div><div className="columns discovery-columns"><div><span className="section-label">EVENTOS ({visibleEvents.length})</span><div className="timeline">{visibleEvents.length ? visibleEvents.map(event => <div className="timeline-row" key={event.id}><span>{event.progress_total ? `${event.progress_done}/${event.progress_total}` : "·"}</span><div><strong>{event.step}</strong><p>{event.message}</p>{(event.payload !== undefined || event.response !== undefined) && <div className="event-details"><div><small>PAYLOAD</small><code>{event.payload ?? "—"}</code></div><div><small>RESPUESTA{event.response_code ? ` · ${event.response_code}` : ""}</small><code>{event.response ?? "—"}</code></div></div>}</div><em>{event.level}</em></div>) : <Empty text="Aún no hay eventos." />}</div></div><div><span className="section-label">INSIGHTS ({insights.length})</span>{insights.length ? <div className="insight-list">{insights.map(insight => <article className="insight" key={insight.id}><strong>{insight.title}</strong><small>{Math.round(insight.confidence * 100)}% confianza</small><p>{insight.content}</p></article>)}</div> : <Empty text="Los insights aparecerán después de la primera observación." />}</div></div></>}</section>;
}
function Mimic({ services }: { services: Service[] }) {
  const [serviceId, setServiceId] = useState(services[0]?.id ?? "");
  const [runs, setRuns] = useState<MimicRun[]>([]);
  const [run, setRun] = useState<MimicRun | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [iterations, setIterations] = useState<MimicIteration[]>([]);
  const [selectedIteration, setSelectedIteration] = useState<number | null>(null);
  const [clone, setClone] = useState<Clone | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!serviceId && services[0]) setServiceId(services[0].id); }, [services, serviceId]);
  async function refresh(runId: string) {
    const [nextRun, nextEvents, nextIterations] = await Promise.all([
      api<MimicRun>(`/api/v1/mimic-runs/${runId}`),
      api<RunEvent[]>(`/api/v1/run-events?mimic_run_id=${runId}`),
      api<MimicIteration[]>(`/api/v1/mimic-runs/${runId}/iterations`),
    ]);
    const orderedEvents = [...nextEvents].sort((left, right) => String(left.created_at ?? left.id).localeCompare(String(right.created_at ?? right.id)));
    const recommended = nextIterations.reduce((best, item) => !best || item.match_rate > best.match_rate ? item : best, nextIterations[0]);
    setRun(nextRun); setEvents(orderedEvents); setIterations(nextIterations); setSelectedIteration(current => current ?? recommended?.iteration ?? null); setRuns(previous => previous.map(item => item.id === nextRun.id ? nextRun : item));
  }
  async function loadRuns(selectedServiceId: string) {
    const [history, clones] = await Promise.all([
      api<MimicRun[]>(`/api/v1/mimic-runs?service_id=${selectedServiceId}`),
      api<Clone[]>("/api/v1/cloned-services"),
    ]);
    setClone(clones.find(item => item.service_id === selectedServiceId) ?? null);
    const ordered = [...history].reverse(); setRuns(ordered);
    if (ordered[0]) await refresh(ordered[0].id); else { setRun(null); setEvents([]); setIterations([]); setSelectedIteration(null); }
  }
  useEffect(() => { if (serviceId) void loadRuns(serviceId).catch(() => undefined); }, [serviceId]);
  useEffect(() => {
    if (!run || ["approval_required", "ready", "failed", "cancelled"].includes(run.status)) return;
    const timer = window.setInterval(() => { void refresh(run.id); }, 2500);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);
  async function startMimic() {
    if (!serviceId) return;
    setBusy(true);
    try {
      const created = await api<MimicRun>(`/api/v1/services/${serviceId}/mimic-runs`, { method: "POST", body: JSON.stringify({ data: {} }) });
      setRuns(previous => [created, ...previous]); setRun(created); setEvents([]); setIterations([]); setSelectedIteration(null); await refresh(created.id);
    } catch (error) { setEvents([{ id: "error", step: "failed", level: "error", message: error instanceof Error ? error.message : "No fue posible iniciar el mimic" }]); }
    finally { setBusy(false); }
  }
  async function approve() {
    if (!run) return;
    setBusy(true);
    try {
      const result = await api<{ run: MimicRun; clone: Clone }>(`/api/v1/mimic-runs/${run.id}/approve`, { method: "POST", body: JSON.stringify({ data: { selected_iteration: selectedIteration } }) });
      setRun(result.run); setClone(result.clone); await refresh(result.run.id);
    } finally { setBusy(false); }
  }
  async function ship(token: string) {
    if (!clone) return;
    const result = await api<{ clone: Clone; token: string }>(`/api/v1/cloned-services/${clone.id}/ship`, { method: "POST", body: JSON.stringify({ data: token ? { token } : {} }) });
    setClone({ ...result.clone, token: result.token });
  }
  let generatedSpec: { service_summary?: string; use_cases?: { name: string; method: string; path: string; behavior: string }[] } | null = null;
  if (run?.code_ref?.startsWith("inline:")) { try { generatedSpec = JSON.parse(run.code_ref.slice(7)); } catch { generatedSpec = null; } }
  const phaseRows = [["design", "Design básico"], ["rules", "Reglas de negocio"], ["tasks", "Tasks trazables"], ["generated", "Implementación"], ["replaying", "Replay"]];
  const phaseEvent = (name: string) => events.find(event => event.step === `${name}_completed`) ?? events.find(event => event.step === name);
  const approvalReady = run?.status === "approval_required" || (run?.status !== "ready" && events.some(event => event.step === "approval_required"));
  const bestIteration = iterations.reduce<MimicIteration | undefined>((best, item) => !best || item.match_rate > best.match_rate ? item : best, undefined);
  const approvedIteration = iterations.find(item => item.selected) ?? iterations.find(item => item.iteration === selectedIteration) ?? bestIteration;
  return <section className="panel"><div className="panel-head"><div><span className="section-label">UC-03 / UC-06</span><h3>Generación y replay</h3></div><div className="discovery-actions"><select disabled={!services.length || busy} value={serviceId} onChange={e => setServiceId(e.target.value)}>{services.map(service => <option key={service.id} value={service.id}>{service.name}</option>)}</select><button disabled={!services.length || busy} onClick={() => void startMimic()}>{busy ? "Iniciando…" : "Nuevo mimic"}</button></div></div>{!services.length ? <Empty text="Registra un servicio antes de iniciar un mimic." /> : <><div className="discovery-status"><strong>{approvalReady ? "approval_required" : run?.status ?? "ready"}</strong><span>{run?.error ?? events[events.length - 1]?.message ?? "El generador usará insights validados y evidencia observada."}</span></div>{run && !approvalReady && !["ready", "failed", "cancelled"].includes(run.status) && <div className="live-update"><span className="live-dot" /> <strong>En vivo</strong><span>Iteración {run.iterations || 1} · actualización automática cada 2.5 s</span></div>}<div className="phase-grid">{phaseRows.map(([key, label]) => <div className={`phase-card ${phaseEvent(key) ? "done" : ""}`} key={key}><span>{phaseEvent(key) ? "✓" : "·"}</span><strong>{label}</strong><small>{phaseEvent(key)?.message ?? "Pendiente"}</small></div>)}</div>{iterations.length > 0 && <><span className="section-label">ITERACIONES DISPONIBLES</span><div className="iteration-picker">{iterations.map(item => <button type="button" className={selectedIteration === item.iteration ? "iteration-card selected" : "iteration-card"} key={item.iteration} onClick={() => setSelectedIteration(item.iteration)}><strong>Iteración {item.iteration}</strong><b>{Math.round(item.match_rate * 100)}%</b><small>{item.matched}/{item.total} requests · {item.use_case_count} use cases</small></button>)}</div></>}{run?.match_rate !== null && run?.match_rate !== undefined && <div className="metric"><small>Replay match rate</small><strong>{Math.round(run.match_rate * 100)}%</strong><span>{run.iterations} iteración(es)</span></div>}{approvalReady && <div className="approve-bar"><div><strong>Listo para revisión</strong><span>{approvedIteration ? `Iteración ${approvedIteration.iteration} · ${approvedIteration.matched}/${approvedIteration.total} tests pasados` : "Revisa el resultado del replay antes de aprobar."}</span></div><button className="btn btn-primary" disabled={busy} onClick={() => void approve()}>{busy ? "Aprobando…" : `Aprobar iteración ${selectedIteration ?? run?.iterations}`}</button></div>}{run?.status === "ready" && clone && <EndpointCard clone={clone} iteration={approvedIteration} onShip={ship} />}<div className="timeline">{[["01", "Insights validados", "El worker selecciona los insights aprobados del servicio."], ["02", "Generación", "El modelo construye contratos de use cases trazables a la evidencia."], ["03", "Replay", "Las secuencias se reproducen contra el runtime generado."], ["04", "Approval required", "La UI muestra el resultado antes de habilitar el clon."]].map(([n, title, text]) => <div className="timeline-row" key={n}><span>{n}</span><div><strong>{title}</strong><p>{text}</p></div><em>{approvalReady ? "APPROVAL_REQUIRED" : run?.status ?? "pendiente"}</em></div>)}</div>{events.length > 0 && <><span className="section-label">EVENTOS EN VIVO</span><div className="timeline">{events.slice(-12).map(event => <div className="timeline-row" key={event.id}><span>·</span><div><strong>{event.step}</strong><p>{event.message}</p></div><em>{event.level}</em></div>)}</div></>}{generatedSpec?.use_cases?.length ? <div className="insight-list"><span className="section-label">USE CASES GENERADOS</span>{generatedSpec.use_cases.map(item => <article className="insight" key={`${item.method}-${item.path}`}><strong>{item.method} {item.path} · {item.name}</strong><p>{item.behavior}</p></article>)}</div> : null}</>}</section>;
}
function EndpointCard({ clone, iteration, onShip }: { clone: Clone; iteration?: MimicIteration; onShip: (token: string) => Promise<void> }) {
  const [token, setToken] = useState("");
  const [shipping, setShipping] = useState(false);
  const [copied, setCopied] = useState("");
  const [error, setError] = useState("");
  const baseUrl = `${API_URL || window.location.origin}/mimic/${clone.slug}`;
  const bearer = clone.token ?? "<token>";
  const rate = iteration ? Math.round(iteration.match_rate * 100) : null;
  const tone = (value: number) => value >= 0.95 ? "good" : value >= 0.6 ? "mid" : "low";
  const copy = (label: string, text: string) => { void navigator.clipboard?.writeText(text); setCopied(label); window.setTimeout(() => setCopied(""), 1500); };
  async function publish() {
    setShipping(true); setError("");
    try { await onShip(token.trim()); setToken(""); } catch (err) { setError(err instanceof Error ? err.message : "No fue posible publicar"); } finally { setShipping(false); }
  }
  const snippets: [string, string][] = [
    ["Health", `curl ${baseUrl}/health -H "Authorization: Bearer ${bearer}"`],
    ["Reset", `curl -X POST ${baseUrl}/__reset -H "Authorization: Bearer ${bearer}"`],
    ["Request", `curl ${baseUrl}${iteration?.endpoints?.find(item => item.method !== "*")?.path ?? "/<ruta>"} -H "Authorization: Bearer ${bearer}"`],
  ];
  return <div className="endpoint-card">
    <div className="endpoint-head"><div><span className="section-label">ENTRYPOINT DEL REEMPLAZO</span><h4>{clone.slug}</h4></div><span className={`state-pill ${clone.enabled ? "on" : "off"}`}><i />{clone.enabled ? "Publicado" : "Aprobado · sin publicar"}</span></div>
    <div className="url-row"><span>BASE URL</span><code>{baseUrl}</code><button className="btn btn-ghost" onClick={() => copy("url", baseUrl)}>{copied === "url" ? "Copiado ✓" : "Copiar"}</button></div>
    {iteration && rate !== null && <div className="stats-row">
      <div className="stat"><small>Iteración aprobada</small><strong>#{iteration.iteration}</strong>{iteration.validated && <span className="tag">validada en DB</span>}</div>
      <div className="stat"><small>Tests pasados</small><strong>{iteration.matched}<em>/{iteration.total}</em></strong><span className="tag">{iteration.total - iteration.matched} fallidos</span></div>
      <div className="stat"><small>Match rate</small><strong className={`tone-${tone(iteration.match_rate)}`}>{rate}%</strong><div className="bar"><span className={`tone-bg-${tone(iteration.match_rate)}`} style={{ width: `${rate}%` }} /></div></div>
      <div className="stat"><small>Use cases</small><strong>{iteration.use_case_count}</strong><span className="tag">handlers generados</span></div>
    </div>}
    {iteration?.endpoints?.length ? <div className="endpoint-table"><span className="section-label">RESULTADO POR ENDPOINT</span>{iteration.endpoints.map(item => { const ratio = item.total ? item.matched / item.total : 0; return <div className="endpoint-row" key={`${item.method} ${item.path}`}><span className={`method m-${item.method === "*" ? "any" : item.method.toLowerCase()}`}>{item.method === "*" ? "ANY" : item.method}</span><code title={item.purpose}>{item.path === "*" ? "rutas inexistentes (404)" : item.path}</code><div className="bar"><span className={`tone-bg-${tone(ratio)}`} style={{ width: `${Math.round(ratio * 100)}%` }} /></div><b>{item.matched}/{item.total}</b></div>; })}</div> : null}
    <div className="ship-row"><input placeholder="Token del equipo (vacío = generar uno)" value={token} onChange={event => setToken(event.target.value)} /><button className="btn btn-primary" disabled={shipping} onClick={() => void publish()}>{shipping ? "Publicando…" : clone.enabled ? "Actualizar token" : "Publicar endpoint"}</button></div>
    {error && <div className="notice">{error}</div>}
    {clone.token && <div className="token-box"><div><small>TOKEN · SE MUESTRA UNA SOLA VEZ</small><code>{clone.token}</code></div><button className="btn btn-ghost" onClick={() => copy("token", clone.token ?? "")}>{copied === "token" ? "Copiado ✓" : "Copiar"}</button></div>}
    <div className="snippets"><span className="section-label">CONTRATO · Authorization: Bearer &lt;token&gt;</span>{snippets.map(([label, command]) => <div className="snippet" key={label}><span>{label}</span><code>{command}</code><button className="btn btn-ghost" onClick={() => copy(label, command)}>{copied === label ? "✓" : "Copiar"}</button></div>)}</div>
  </div>;
}
function Compare() { return <section className="panel"><div className="panel-head"><div><span className="section-label">UC-05</span><h3>Comparador original vs. clon</h3></div><span className="pill">EN MEMORIA</span></div><Empty text="Aquí podrás enviar una request al original y al clon y revisar su diff." /></section>; }
function Metric({ label, value, hint }: { label: string; value: string | number; hint: string }) { return <div className="metric"><small>{label}</small><strong>{value}</strong><span>{hint}</span></div>; }
function Empty({ text }: { text: string }) { return <div className="empty"><span>✦</span><p>{text}</p></div>; }

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("[frontier] Missing #root element");
}

console.log("[frontier] mounting React app");
createRoot(rootElement).render(<App />);

export default App;
