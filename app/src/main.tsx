import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import "./styles.css";

type View = "overview" | "services" | "discovery" | "mimic" | "compare";
type Service = { id: string; name: string; base_url: string; status: string; request_interval_ms: number };
const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) throw new Error(await res.text());
  return res.status === 204 ? (undefined as T) : res.json();
}

function App() {
  const [view, setView] = useState<View>("overview");
  const [services, setServices] = useState<Service[]>([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ name: "", base_url: "", request_interval_ms: 250 });
  const loadServices = () => api<Service[]>("/api/v1/services").then(setServices).catch(() => setError("La API aún no está conectada."));
  useEffect(() => { void loadServices(); }, []);
  async function createService(event: FormEvent) {
    event.preventDefault(); setError("");
    try { await api("/api/v1/services", { method: "POST", body: JSON.stringify(form) }); setForm({ name: "", base_url: "", request_interval_ms: 250 }); await loadServices(); setView("services"); }
    catch { setError("No fue posible registrar el servicio."); }
  }
  const navigation: [View, string, string][] = [["overview", "Overview", "⌂"], ["services", "Servicios", "◈"], ["discovery", "Discovery", "◎"], ["mimic", "Mimic & replay", "◇"], ["compare", "Comparador", "⇄"]];
  return <div className="layout"><aside className="sidebar"><div className="brand"><span className="brand-mark">F</span><span>frontier</span></div><nav>{navigation.map(([key, label, icon]) => <button key={key} className={view === key ? "nav active" : "nav"} onClick={() => setView(key)}><span>{icon}</span>{label}</button>)}</nav><div className="sidebar-foot"><span className="dot" /> Sistema operativo<br /><small>Railway · development</small></div></aside><main className="main"><header><div><span className="section-label">PLATAFORMA</span><h1>{navigation.find(([key]) => key === view)?.[1]}</h1></div><div className="user-chip">DR <span>David Rojas</span></div></header>{error && <div className="notice">{error}</div>}{view === "overview" && <Overview services={services} onAction={() => setView("services")} />}{view === "services" && <Services services={services} form={form} setForm={setForm} onSubmit={createService} />}{view === "discovery" && <Discovery services={services} />}{view === "mimic" && <Mimic services={services} />}{view === "compare" && <Compare />}</main></div>;
}

function Overview({ services, onAction }: { services: Service[]; onAction: () => void }) { return <><div className="hero"><div><span className="pill">BLACK BOX INTELLIGENCE</span><h2>Entiende lo que no puedes ver.</h2><p>Descubre el comportamiento de un servicio, genera su reemplazo y valida la equivalencia.</p><button onClick={onAction}>Registrar servicio <span>→</span></button></div><div className="hero-art"><i /><i /><i /><i /></div></div><div className="metric-grid"><Metric label="Servicios registrados" value={services.length} hint="en tu workspace" /><Metric label="Discoveries activos" value="—" hint="listos para ejecutar" /><Metric label="Clones habilitados" value="—" hint="esperando ship" /></div><div className="panel"><div className="panel-head"><div><span className="section-label">FLUJO PRINCIPAL</span><h3>Del descubrimiento al ship</h3></div><span className="muted">4 etapas</span></div><div className="flow">{["Configuración", "Discovery", "Mimic", "Ship"].map((x, i) => <div className="flow-step" key={x}><span>{String(i + 1).padStart(2, "0")}</span><strong>{x}</strong><small>{["Define el servicio", "Captura evidencia", "Genera el clon", "Habilita el acceso"][i]}</small></div>)}</div></div></>; }
function Services({ services, form, setForm, onSubmit }: { services: Service[]; form: { name: string; base_url: string; request_interval_ms: number }; setForm: (v: typeof form) => void; onSubmit: (e: FormEvent) => void }) { return <div className="columns"><section className="panel"><div className="panel-head"><div><span className="section-label">UC-01</span><h3>Registrar caja negra</h3></div></div><form onSubmit={onSubmit} className="form"><label>Nombre del servicio<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Banco legacy" /></label><label>URL base<input required type="url" value={form.base_url} onChange={e => setForm({ ...form, base_url: e.target.value })} placeholder="https://api.example.com" /></label><label>Intervalo entre requests (ms)<input type="number" min="0" value={form.request_interval_ms} onChange={e => setForm({ ...form, request_interval_ms: Number(e.target.value) })} /></label><button type="submit">Guardar servicio <span>→</span></button></form></section><section className="panel"><div className="panel-head"><div><span className="section-label">INVENTARIO</span><h3>Servicios registrados</h3></div><span className="count">{services.length}</span></div>{services.length === 0 ? <Empty text="Todavía no hay servicios registrados." /> : <div className="service-list">{services.map(service => <div className="service-row" key={service.id}><span className="service-icon">◈</span><div><strong>{service.name}</strong><small>{service.base_url}</small></div><span className="status"><i />{service.status}</span></div>)}</div>}</section></div>; }
function Discovery({ services }: { services: Service[] }) { return <section className="panel"><div className="panel-head"><div><span className="section-label">UC-02 / UC-04</span><h3>Discovery runs</h3></div><button disabled={!services.length}>Iniciar discovery</button></div><Empty text={services.length ? "Selecciona un servicio para iniciar la exploración." : "Registra un servicio antes de iniciar discovery."} /></section>; }
function Mimic({ services }: { services: Service[] }) { return <section className="panel"><div className="panel-head"><div><span className="section-label">UC-03 / UC-06</span><h3>Generación y replay</h3></div><button disabled={!services.length}>Nuevo mimic</button></div><div className="timeline">{[["01", "Insights validados", "Selecciona el conocimiento que alimentará el generador."], ["02", "Generación", "El agente crea los use cases del clon."], ["03", "Replay", "Compara secuencias contra la evidencia original."], ["04", "Approval required", "Aprueba el resultado antes de hacer ship."]].map(([n, title, text]) => <div className="timeline-row" key={n}><span>{n}</span><div><strong>{title}</strong><p>{text}</p></div><em>pendiente</em></div>)}</div></section>; }
function Compare() { return <section className="panel"><div className="panel-head"><div><span className="section-label">UC-05</span><h3>Comparador original vs. clon</h3></div><span className="pill">EN MEMORIA</span></div><Empty text="Aquí podrás enviar una request al original y al clon y revisar su diff." /></section>; }
function Metric({ label, value, hint }: { label: string; value: string | number; hint: string }) { return <div className="metric"><small>{label}</small><strong>{value}</strong><span>{hint}</span></div>; }
function Empty({ text }: { text: string }) { return <div className="empty"><span>✦</span><p>{text}</p></div>; }
export default App;
