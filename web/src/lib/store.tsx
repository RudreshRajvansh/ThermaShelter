import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { DesignSpec, ParetoResult, Window } from "./api";

export const OUTPUT_TABS = [
  "recommendation",
  "3d model",
  "climate",
  "construction",
  "performance",
  "compare",
  "report",
] as const;

export type OutputTab = (typeof OUTPUT_TABS)[number];
export type Page = "input" | "output";
export type ThemePreference = "system" | "light" | "dark";

export const WINTER: Window = { start: "2019-01-10", end: "2019-01-16" };
export const SUMMER: Window = { start: "2019-06-10", end: "2019-06-16" };
export const SURFACE_WINTER: Window = { start: "2019-01-12", end: "2019-01-14" };
export const SURFACE_SUMMER: Window = { start: "2019-06-12", end: "2019-06-14" };

export type DisplayMode = "construction" | "surface_temperature" | "incident_solar" | "interior_temperature";

export type Preview = { design: DesignSpec; label: string; hash: string };

export type SearchBudget = { designs: number; size: number; label: string; minutes: number };

export const BUDGETS: SearchBudget[] = [
  { designs: 100, size: 20, label: "Quick look", minutes: 6 },
  { designs: 300, size: 25, label: "Standard", minutes: 18 },
  { designs: 500, size: 25, label: "Thorough", minutes: 31 },
];

export type SearchRequest = { token: number; budget: SearchBudget };

export type SearchState = {
  token: number | null;
  jobId: string | null;
  result: ParetoResult | null;
  failure: string | null;
};

const IDLE_SEARCH: SearchState = { token: null, jobId: null, result: null, failure: null };
const THEME_KEY = "entropy-zero-theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

type State = {
  page: Page;
  setPage: (page: Page) => void;
  outputTab: OutputTab;
  open: (tab: OutputTab) => void;
  theme: ThemePreference;
  resolvedTheme: "light" | "dark";
  setTheme: (theme: ThemePreference) => void;
  searchRequest: SearchRequest | null;
  requestSearch: (budget: SearchBudget) => void;
  search: SearchState;
  updateSearch: (patch: Partial<SearchState>) => void;
  siteId: string | null;
  setSiteId: (id: string) => void;
  designName: string;
  setDesignName: (name: string) => void;
  design: DesignSpec | null;
  setDesign: (design: DesignSpec | null) => void;
  overrides: DesignSpec | null;
  applyOverride: (design: DesignSpec) => void;
  clearOverride: () => void;
  displayMode: DisplayMode;
  setDisplayMode: (mode: DisplayMode) => void;
  hourIndex: number;
  setHourIndex: (index: number) => void;
  hourSeeded: boolean;
  seedHour: (index: number) => void;
  cooling: boolean;
  setCooling: (value: boolean) => void;
  period: Window;
  surfacePeriod: Window;
  liveMode: boolean;
  comfortModelId: string | null;
  setComfortModelId: (value: string | null) => void;
  setLiveMode: (value: boolean) => void;
  preview: Preview | null;
  setPreview: (value: Preview | null) => void;
};

const Context = createContext<State | null>(null);

function readLocation(): { page: Page; tab: OutputTab } {
  const parts = window.location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "results") {
    const tab = decodeURIComponent(parts[1] ?? "") as OutputTab;
    return { page: "output", tab: OUTPUT_TABS.includes(tab) ? tab : "recommendation" };
  }
  return { page: "input", tab: "recommendation" };
}

function hashFor(page: Page, tab: OutputTab): string {
  return page === "input" ? "#/input" : `#/results/${encodeURIComponent(tab)}`;
}

function readTheme(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

function systemPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const initial = readLocation();
  const [page, setPageState] = useState<Page>(initial.page);
  const [outputTab, setOutputTab] = useState<OutputTab>(initial.tab);
  const [theme, setThemeState] = useState<ThemePreference>(readTheme);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);
  const [searchRequest, setSearchRequest] = useState<SearchRequest | null>(null);
  const [search, setSearch] = useState<SearchState>(IDLE_SEARCH);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [designName, setDesignName] = useState("single_room");
  const [design, setDesign] = useState<DesignSpec | null>(null);
  const [overrides, setOverrides] = useState<DesignSpec | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [displayMode, setDisplayMode] = useState<DisplayMode>("construction");
  const [hourIndex, setHourIndex] = useState(0);
  const [hourSeeded, setHourSeeded] = useState(false);
  const [cooling, setCooling] = useState(false);
  const [liveMode, setLiveMode] = useState(false);
  const [comfortModelId, setComfortModelId] = useState<string | null>(null);

  const resolvedTheme: "light" | "dark" =
    theme === "system" ? (systemDark ? "dark" : "light") : theme;

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(DARK_QUERY);
    const follow = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolvedTheme);
  }, [resolvedTheme]);

  useEffect(() => {
    const target = hashFor(page, outputTab);
    if (window.location.hash !== target) window.history.pushState(null, "", target);
  }, [page, outputTab]);

  useEffect(() => {
    const follow = () => {
      const next = readLocation();
      setPageState(next.page);
      setOutputTab(next.tab);
    };
    window.addEventListener("popstate", follow);
    window.addEventListener("hashchange", follow);
    return () => {
      window.removeEventListener("popstate", follow);
      window.removeEventListener("hashchange", follow);
    };
  }, []);

  const setTheme = useCallback((next: ThemePreference) => {
    setThemeState(next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      return;
    }
  }, []);
  const setPage = useCallback((next: Page) => {
    setPageState(next);
    window.scrollTo({ top: 0 });
  }, []);
  const open = useCallback((tab: OutputTab) => {
    setPageState("output");
    setOutputTab(tab);
    window.scrollTo({ top: 0 });
  }, []);
  const requestSearch = useCallback((budget: SearchBudget) => {
    setSearchRequest({ token: Date.now(), budget });
    setPreview(null);
    setPageState("output");
    setOutputTab("recommendation");
    window.scrollTo({ top: 0 });
  }, []);
  const updateSearch = useCallback((patch: Partial<SearchState>) => {
    setSearch((current) => ({ ...current, ...patch }));
  }, []);
  const applyOverride = useCallback((next: DesignSpec) => {
    setOverrides(next);
    setPreview(null);
  }, []);
  const selectDesign = useCallback((name: string) => {
    setDesignName(name);
    setOverrides(null);
    setPreview(null);
  }, []);
  const clearOverride = useCallback(() => {
    setOverrides(null);
    setPreview(null);
  }, []);

  const value = useMemo<State>(
    () => ({
      page,
      setPage,
      outputTab,
      open,
      theme,
      resolvedTheme,
      setTheme,
      searchRequest,
      requestSearch,
      search,
      updateSearch,
      siteId,
      setSiteId,
      designName,
      setDesignName: selectDesign,
      design,
      setDesign,
      overrides,
      applyOverride,
      clearOverride,
      displayMode,
      setDisplayMode,
      hourIndex,
      setHourIndex,
      hourSeeded,
      seedHour: (index: number) => {
        setHourSeeded(true);
        setHourIndex(index);
      },
      cooling,
      setCooling,
      period: cooling ? SUMMER : WINTER,
      surfacePeriod: cooling ? SURFACE_SUMMER : SURFACE_WINTER,
      liveMode,
      comfortModelId,
      setComfortModelId,
      setLiveMode,
      preview,
      setPreview,
    }),
    [
      page,
      setPage,
      outputTab,
      open,
      theme,
      resolvedTheme,
      setTheme,
      searchRequest,
      requestSearch,
      search,
      updateSearch,
      siteId,
      designName,
      selectDesign,
      design,
      overrides,
      applyOverride,
      clearOverride,
      displayMode,
      hourIndex,
      hourSeeded,
      cooling,
      liveMode,
      comfortModelId,
      preview,
    ],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useStore(): State {
  const value = useContext(Context);
  if (!value) throw new Error("useStore must be used inside StoreProvider");
  return value;
}

export function activeDesign(state: State): DesignSpec | null {
  return state.overrides ?? state.design;
}

export function describeDesign(design: DesignSpec): string {
  const words = design.arrangement.replace(/_/g, " ");
  const arrangement = words.charAt(0).toUpperCase() + words.slice(1);
  const wall = design.wall.layers.reduce(
    (total: number, layer: { thickness_m: number }) => total + layer.thickness_m,
    0,
  );
  return `${arrangement}, ${Math.round(wall * 1000)} mm wall`;
}
