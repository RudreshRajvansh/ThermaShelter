import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { DesignSpec, Window } from "./api";

export const STEPS = [
  "Design a shelter",
  "Site Input",
  "Climate Data",
  "Design Parameters",
  "Simulation",
  "Results",
  "Compare Designs",
  "Reports",
] as const;
export type Step = (typeof STEPS)[number];

export const WINTER: Window = { start: "2019-01-10", end: "2019-01-16" };
export const SUMMER: Window = { start: "2019-06-10", end: "2019-06-16" };
export const SURFACE_WINTER: Window = { start: "2019-01-12", end: "2019-01-14" };
export const SURFACE_SUMMER: Window = { start: "2019-06-12", end: "2019-06-14" };

export type DisplayMode = "construction" | "surface_temperature" | "incident_solar";

type State = {
  step: Step;
  setStep: (step: Step) => void;
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
  cooling: boolean;
  setCooling: (value: boolean) => void;
  period: Window;
  surfacePeriod: Window;
  liveMode: boolean;
  comfortModelId: string | null;
  setComfortModelId: (value: string | null) => void;
  setLiveMode: (value: boolean) => void;
};

const Context = createContext<State | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [step, setStep] = useState<Step>("Design a shelter");
  const [siteId, setSiteId] = useState<string | null>(null);
  const [designName, setDesignName] = useState("single_room");
  const [design, setDesign] = useState<DesignSpec | null>(null);
  const [overrides, setOverrides] = useState<DesignSpec | null>(null);
  const [displayMode, setDisplayMode] = useState<DisplayMode>("construction");
  const [hourIndex, setHourIndex] = useState(0);
  const [cooling, setCooling] = useState(false);
  const [liveMode, setLiveMode] = useState(false);
  const [comfortModelId, setComfortModelId] = useState<string | null>(null);

  const applyOverride = useCallback((next: DesignSpec) => setOverrides(next), []);
  const selectDesign = useCallback((name: string) => {
    setDesignName(name);
    setOverrides(null);
  }, []);
  const clearOverride = useCallback(() => setOverrides(null), []);

  const value = useMemo<State>(
    () => ({
      step,
      setStep,
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
      cooling,
      setCooling,
      period: cooling ? SUMMER : WINTER,
      surfacePeriod: cooling ? SURFACE_SUMMER : SURFACE_WINTER,
      liveMode,
      comfortModelId,
      setComfortModelId,
      setLiveMode,
    }),
    [
      step,
      siteId,
      designName,
      selectDesign,
      design,
      overrides,
      applyOverride,
      clearOverride,
      displayMode,
      hourIndex,
      cooling,
      liveMode,
      comfortModelId,
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
