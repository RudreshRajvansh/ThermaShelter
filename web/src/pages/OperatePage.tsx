import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type CommanderCard,
  type MetaResponse,
  type Post,
} from "../lib/api";
import { Empty, Failure, Loading, Panel } from "../components/Kit";
import { activeDesign, useStore } from "../lib/store";
import { num } from "../lib/format";

type Lang = "en" | "hi";

const LABELS: Record<Lang, Record<string, string>> = {
  en: {
    title: "Tonight at every post",
    subtitle:
      "Each post is simulated on the last 7 days and the next 48 hours of forecast for its own spot, then turned into orders.",
    issueAll: "Issue every card",
    issuing: "Running EnergyPlus for every post…",
    none: "No posts registered yet",
    noneDetail: "Register a post below with the design as it was built.",
    post: "Post",
    soldiers: "Soldiers",
    stock: "Fuel stock",
    status: "Tonight",
    issued: "Issued",
    issue: "Issue card",
    view: "Card",
    remove: "Remove",
    notIssued: "no card yet",
    register: "Register a post",
    registerDetail:
      "The post takes the design and site now open in Inputs, as built.",
    id: "Post id",
    name: "Name",
    lat: "Latitude",
    lon: "Longitude",
    fuel: "Heater fuel",
    save: "Save post",
    card: "Commander Card",
    night: "Night of",
    insideMin: "Coldest inside",
    outsideMin: "Coldest outside",
    below: "Hours below",
    fuelTonight: "Fuel tonight",
    fuelOut: "Fuel lasts",
    freshAir: "Fresh air",
    modelError: "Model error",
    uncalibrated: "not calibrated",
    source: "Weather",
    orders: "Orders",
    safety: "Safety",
    print: "Print A5",
    red: "red",
    amber: "amber",
    green: "green",
    hours: "h",
    at: "at",
    forecast: "forecast",
    "cached forecast": "cached forecast",
    climatology: "typical year, no forecast",
  },
  hi: {
    title: "आज रात हर पोस्ट पर",
    subtitle:
      "हर पोस्ट को उसकी अपनी जगह के पिछले 7 दिन और अगले 48 घंटे के पूर्वानुमान पर चलाया जाता है, फिर आदेश बनते हैं।",
    issueAll: "सभी कार्ड जारी करें",
    issuing: "हर पोस्ट के लिए EnergyPlus चल रहा है…",
    none: "अभी कोई पोस्ट दर्ज नहीं",
    noneDetail: "नीचे पोस्ट को उसके बने हुए डिज़ाइन के साथ दर्ज करें।",
    post: "पोस्ट",
    soldiers: "सैनिक",
    stock: "ईंधन भंडार",
    status: "आज रात",
    issued: "जारी",
    issue: "कार्ड जारी करें",
    view: "कार्ड",
    remove: "हटाएँ",
    notIssued: "अभी कार्ड नहीं",
    register: "पोस्ट दर्ज करें",
    registerDetail:
      "पोस्ट वही डिज़ाइन और जगह लेती है जो अभी इनपुट में खुली है।",
    id: "पोस्ट आईडी",
    name: "नाम",
    lat: "अक्षांश",
    lon: "देशांतर",
    fuel: "हीटर ईंधन",
    save: "पोस्ट सहेजें",
    card: "कमांडर कार्ड",
    night: "रात",
    insideMin: "अंदर न्यूनतम",
    outsideMin: "बाहर न्यूनतम",
    below: "घंटे नीचे",
    fuelTonight: "आज रात ईंधन",
    fuelOut: "ईंधन चलेगा",
    freshAir: "ताज़ी हवा",
    modelError: "मॉडल त्रुटि",
    uncalibrated: "कैलिब्रेट नहीं",
    source: "मौसम",
    orders: "आदेश",
    safety: "सुरक्षा",
    print: "A5 छापें",
    red: "लाल",
    amber: "पीला",
    green: "हरा",
    hours: "घंटे",
    at: "पर",
    forecast: "पूर्वानुमान",
    "cached forecast": "सहेजा पूर्वानुमान",
    climatology: "सामान्य वर्ष, पूर्वानुमान नहीं",
  },
};

const STATUS_STYLE: Record<string, string> = {
  red: "bg-[var(--color-diesel)] text-white",
  amber: "bg-[var(--color-solar)] text-white",
  green: "bg-[var(--color-good)] text-white",
};

function Pill({
  status,
  lang,
}: {
  status: string | null | undefined;
  lang: Lang;
}) {
  if (!status)
    return (
      <span className="text-xs text-[var(--color-ink-faint)]">
        {LABELS[lang].notIssued}
      </span>
    );
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase ${STATUS_STYLE[status]}`}
    >
      {LABELS[lang][status]}
    </span>
  );
}

function Card({ card, lang }: { card: CommanderCard; lang: Lang }) {
  const t = LABELS[lang];
  return (
    <div className="a5-card rounded-lg border border-[var(--color-line)] bg-[var(--color-day)] p-4 text-[var(--color-ink)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[var(--color-ink-faint)]">
            {t.card}
          </p>
          <p className="display text-lg font-semibold">
            {card.post_name} · {t.night} {card.night_of}
          </p>
          <p className="text-[11px] text-[var(--color-ink-faint)]">
            {t.issued} {card.issued_at.replace("T", " ").slice(0, 16)} ·{" "}
            {t.source}: {t[card.forecast_source] ?? card.forecast_source}
            {card.forecast_age_h != null
              ? ` (${num(card.forecast_age_h, 1)} ${t.hours})`
              : ""}
          </p>
        </div>
        <Pill status={card.status} lang={lang} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
        <div>
          <p className="text-[11px] text-[var(--color-ink-faint)]">
            {t.insideMin}
          </p>
          <p className="tnum font-semibold">
            {num(card.inside_min_c, 1)} °C {t.at} {card.inside_min_at}
          </p>
        </div>
        <div>
          <p className="text-[11px] text-[var(--color-ink-faint)]">
            {t.outsideMin}
          </p>
          <p className="tnum font-semibold">{num(card.outside_min_c, 1)} °C</p>
        </div>
        <div>
          <p className="text-[11px] text-[var(--color-ink-faint)]">
            {t.below} {num(card.target_c, 0)} °C
          </p>
          <p className="tnum font-semibold">
            {num(card.hours_below_target, 0)} {t.hours}
          </p>
        </div>
        <div>
          <p className="text-[11px] text-[var(--color-ink-faint)]">
            {t.fuelTonight}
          </p>
          <p className="tnum font-semibold">
            {num(card.fuel_tonight_units, 1)} {card.fuel_unit}
          </p>
        </div>
        <div>
          <p className="text-[11px] text-[var(--color-ink-faint)]">
            {t.fuelOut}
          </p>
          <p className="tnum font-semibold">
            {card.fuel_out_hours != null
              ? `${num(card.fuel_out_hours, 0)} ${t.hours}`
              : "—"}
          </p>
        </div>
        <div>
          <p className="text-[11px] text-[var(--color-ink-faint)]">
            {t.modelError}
          </p>
          <p className="tnum font-semibold">
            {card.model_error_k != null
              ? `± ${num(card.model_error_k, 1)} K`
              : t.uncalibrated}
          </p>
        </div>
      </div>
      <p className="mt-4 text-[11px] font-medium uppercase tracking-wide text-[var(--color-ink-faint)]">
        {t.orders}
      </p>
      <ol className="mt-1 space-y-1.5 text-sm">
        {card.orders.map((order) => (
          <li key={order.key} className="flex gap-3">
            <span className="tnum w-20 shrink-0 font-semibold">
              {order.time}
            </span>
            <span>{order.action}</span>
          </li>
        ))}
      </ol>
      <p className="mt-4 text-[11px] font-medium uppercase tracking-wide text-[var(--color-ink-faint)]">
        {t.safety}
      </p>
      {card.safety.map((line) => (
        <p
          key={line}
          className="text-sm font-medium text-[var(--color-danger-ink)]"
        >
          {line}
        </p>
      ))}
      {(card.notes ?? []).map((note) => (
        <p
          key={note}
          className="mt-2 text-[11px] text-[var(--color-ink-faint)]"
        >
          {note}
        </p>
      ))}
    </div>
  );
}

export function OperatePage({ meta }: { meta: MetaResponse | undefined }) {
  const store = useStore();
  const client = useQueryClient();
  const [lang, setLang] = useState<Lang>("en");
  const [selected, setSelected] = useState<string | null>(null);
  const t = LABELS[lang];
  const posts = useQuery({
    queryKey: ["posts", lang],
    queryFn: () => api.posts(lang),
  });
  const card = useQuery({
    queryKey: ["card", selected, lang],
    queryFn: () => api.lastCard(selected!, lang),
    enabled: Boolean(selected),
    retry: false,
  });
  const refresh = () => {
    client.invalidateQueries({ queryKey: ["posts"] });
    client.invalidateQueries({ queryKey: ["card"] });
  };
  const issueOne = useMutation({
    mutationFn: (id: string) => api.issueCard(id, lang),
    onSuccess: (_, id) => {
      setSelected(id);
      refresh();
    },
  });
  const issueEvery = useMutation({
    mutationFn: () => api.issueAll(lang),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deletePost(id),
    onSuccess: (_, id) => {
      if (selected === id) setSelected(null);
      refresh();
    },
  });
  const site = meta?.sites.find((item) => item.id === store.siteId);
  const design = activeDesign(store);
  const [form, setForm] = useState({
    id: "",
    name: "",
    soldiers: "20",
    stock: "",
    fuel: "kerosene",
    lat: "",
    lon: "",
  });
  const save = useMutation({
    mutationFn: () => {
      const body: Post = {
        id: form.id.trim(),
        name: form.name.trim() || form.id.trim(),
        site_id: store.siteId!,
        latitude_deg: Number(form.lat || site?.latitude_deg),
        longitude_deg: Number(form.lon || site?.longitude_deg),
        design: design!,
        soldiers: Number(form.soldiers),
        fuel: form.fuel as Post["fuel"],
        stock_units: form.stock === "" ? null : Number(form.stock),
      };
      return api.savePost(body);
    },
    onSuccess: refresh,
  });
  const input =
    "w-full rounded-md border border-[var(--color-line)] bg-[var(--color-night)] px-2 py-1.5 text-sm";

  return (
    <div className="space-y-4">
      <div className="no-print">
        <Panel
          title={t.title}
          subtitle={t.subtitle}
          right={
            <div className="no-print flex items-center gap-2">
              <div className="flex rounded-md border border-[var(--color-line)] text-xs">
                {(["en", "hi"] as Lang[]).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setLang(item)}
                    className={`px-2 py-1 ${lang === item ? "bg-[var(--color-solar)] text-white" : ""}`}
                  >
                    {item === "en" ? "English" : "हिन्दी"}
                  </button>
                ))}
              </div>
              <button
                type="button"
                disabled={issueEvery.isPending || !posts.data?.length}
                onClick={() => issueEvery.mutate()}
                className="rounded-md bg-[var(--color-solar)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                {issueEvery.isPending ? t.issuing : t.issueAll}
              </button>
            </div>
          }
        >
          {posts.error && <Failure error={posts.error} />}
          {issueEvery.error && <Failure error={issueEvery.error} />}
          {issueOne.error && <Failure error={issueOne.error} />}
          {!posts.data && !posts.error && (
            <Loading label="reading the posts…" />
          )}
          {posts.data && posts.data.length === 0 && (
            <Empty title={t.none} detail={t.noneDetail} />
          )}
          {posts.data && posts.data.length > 0 && (
            <table className="w-full text-sm">
              <thead className="text-[11px] text-[var(--color-ink-faint)]">
                <tr>
                  <th className="py-1 text-left font-normal">{t.status}</th>
                  <th className="py-1 text-left font-normal">{t.post}</th>
                  <th className="py-1 text-right font-normal">{t.soldiers}</th>
                  <th className="py-1 text-right font-normal">{t.stock}</th>
                  <th className="py-1 pl-4 text-left font-normal">{t.issued}</th>
                  <th className="py-1" />
                </tr>
              </thead>
              <tbody>
                {posts.data.map((item) => (
                  <tr
                    key={item.post.id}
                    className="border-t border-[var(--color-line-soft)]"
                  >
                    <td className="py-1.5">
                      <Pill status={item.status} lang={lang} />
                    </td>
                    <td className="py-1.5">
                      <span className="font-medium">{item.post.id}</span>{" "}
                      <span className="text-[var(--color-ink-faint)]">
                        {item.post.name}
                      </span>
                    </td>
                    <td className="tnum py-1.5 text-right">
                      {item.post.soldiers}
                    </td>
                    <td className="tnum py-1.5 text-right">
                      {item.post.stock_units != null
                        ? `${num(item.post.stock_units, 1)} L`
                        : "—"}
                    </td>
                    <td className="py-1.5 pl-4 text-xs text-[var(--color-ink-faint)]">
                      {item.issued_at
                        ? String(item.issued_at).replace("T", " ").slice(0, 16)
                        : "—"}
                    </td>
                    <td className="no-print py-1.5 text-right text-xs">
                      <button
                        type="button"
                        className="mr-2 underline"
                        disabled={issueOne.isPending}
                        onClick={() => issueOne.mutate(item.post.id)}
                      >
                        {issueOne.isPending &&
                        issueOne.variables === item.post.id
                          ? "…"
                          : t.issue}
                      </button>
                      {item.status && (
                        <button
                          type="button"
                          className="mr-2 underline"
                          onClick={() => setSelected(item.post.id)}
                        >
                          {t.view}
                        </button>
                      )}
                      <button
                        type="button"
                        className="underline text-[var(--color-danger-ink)]"
                        onClick={() => remove.mutate(item.post.id)}
                      >
                        {t.remove}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

      {selected && (
        <div className="space-y-2">
          <div className="no-print flex justify-end">
            <button
              type="button"
              onClick={() => window.print()}
              className="rounded-md border border-[var(--color-line)] px-3 py-1.5 text-xs"
            >
              {t.print}
            </button>
          </div>
          {card.error && <Failure error={card.error} />}
          {card.data ? (
            <Card card={card.data} lang={lang} />
          ) : (
            !card.error && <Loading label="…" />
          )}
        </div>
      )}

      <div className="no-print">
        <Panel title={t.register} subtitle={t.registerDetail}>
          {!design || !store.siteId ? (
            <Empty title={t.none} detail={t.registerDetail} />
          ) : (
            <form
              className="no-print grid gap-3 sm:grid-cols-4"
              onSubmit={(event) => {
                event.preventDefault();
                save.mutate();
              }}
            >
              <label className="text-xs">
                {t.id}
                <input
                  className={input}
                  required
                  pattern="[A-Za-z0-9_\-]{1,32}"
                  value={form.id}
                  onChange={(event) =>
                    setForm({ ...form, id: event.target.value })
                  }
                />
              </label>
              <label className="text-xs">
                {t.name}
                <input
                  className={input}
                  value={form.name}
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
                />
              </label>
              <label className="text-xs">
                {t.soldiers}
                <input
                  className={input}
                  type="number"
                  min={1}
                  max={500}
                  required
                  value={form.soldiers}
                  onChange={(event) =>
                    setForm({ ...form, soldiers: event.target.value })
                  }
                />
              </label>
              <label className="text-xs">
                {t.stock} (L)
                <input
                  className={input}
                  type="number"
                  min={0}
                  step="0.1"
                  value={form.stock}
                  onChange={(event) =>
                    setForm({ ...form, stock: event.target.value })
                  }
                />
              </label>
              <label className="text-xs">
                {t.lat}
                <input
                  className={input}
                  type="number"
                  step="0.00001"
                  min={-90}
                  max={90}
                  placeholder={site ? String(site.latitude_deg) : ""}
                  value={form.lat}
                  onChange={(event) =>
                    setForm({ ...form, lat: event.target.value })
                  }
                />
              </label>
              <label className="text-xs">
                {t.lon}
                <input
                  className={input}
                  type="number"
                  step="0.00001"
                  min={-180}
                  max={180}
                  placeholder={site ? String(site.longitude_deg) : ""}
                  value={form.lon}
                  onChange={(event) =>
                    setForm({ ...form, lon: event.target.value })
                  }
                />
              </label>
              <label className="text-xs">
                {t.fuel}
                <select
                  className={input}
                  value={form.fuel}
                  onChange={(event) =>
                    setForm({ ...form, fuel: event.target.value })
                  }
                >
                  {["kerosene", "diesel", "lpg", "wood", "electricity"].map(
                    (fuel) => (
                      <option key={fuel} value={fuel}>
                        {fuel}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <div className="flex items-end">
                <button
                  type="submit"
                  disabled={save.isPending}
                  className="w-full rounded-md bg-[var(--color-solar)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                >
                  {t.save}
                </button>
              </div>
              <p className="text-[11px] text-[var(--color-ink-faint)] sm:col-span-4">
                {design.name} · {site?.name ?? store.siteId}
              </p>
              {save.error && (
                <div className="sm:col-span-4">
                  <Failure error={save.error} />
                </div>
              )}
            </form>
          )}
        </Panel>
      </div>
    </div>
  );
}
