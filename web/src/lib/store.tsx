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
  "Site Input",
  "Climate Data",
  "Design Parameters",
  "Simulation",
  "Results",
  "Compare Designs",
  "Reports"
] as const;

export type Step = (typeof STEPS)[number];

export const WINTER: Window = { start: "2019-01-10", end: "2019-01-16" };
export const SUMMER: Window = { start: "2019-06-10", end: "2019-06-16" };
export const SURFACE_WINTER: Window = { start: "2019-01-12", end: "2019-01-14" };
export const SURFACE_SUMMER: Window = { start: "2019-06-12", end: "2019-06-14" };

export type DisplayMode = "construction" | "surface_temperature" | "incident_solar" | "interior_temperature";

export type Preview = { design: DesignSpec; label: string; hash: string };

type State = {
  step: Step;
  setStep: (step: Step) => void;
  go: (step: Step) => void;
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
  preview: Preview | null;
  setPreview: (value: Preview | null) => void;
};

const Context = createContext<State | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [step, setStep] = useState<Step>("Site Input");
  const [siteId, setSiteId] = useState<string | null>(null);
  const [designName, setDesignName] = useState("single_room");
  const [design, setDesign] = useState<DesignSpec | null>(null);
  const [overrides, setOverrides] = useState<DesignSpec | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [displayMode, setDisplayMode] = useState<DisplayMode>("construction");
  const [hourIndex, setHourIndex] = useState(0);
  const [cooling, setCooling] = useState(false);
  const [liveMode, setLiveMode] = useState(false);
  const [comfortModelId, setComfortModelId] = useState<string | null>(null);

  const go = useCallback((next: Step) => {
    setStep(next);
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
      step,
      setStep,
      go,
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
      preview,
      setPreview,
    }),
    [
      step,
      go,
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
