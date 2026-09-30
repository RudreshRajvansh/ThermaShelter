import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CircleMarker,
  LayersControl,
  MapContainer,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";
import type { LatLngTuple } from "leaflet";
import "leaflet/dist/leaflet.css";
import { api, type MetaResponse, type SiteDetail } from "../lib/api";
import { Failure, Field, Loading, Warnings } from "./Kit";
import { HorizonChart } from "./HorizonChart";
import { num } from "../lib/format";

const LEH: LatLngTuple = [34.1526, 77.5771];

type Pending = { latitude: number; longitude: number; name: string | null };
type Flight = { point: LatLngTuple; zoom: number; key: number };

export function formatCoordinate(latitude: number, longitude: number): string {
  return `${Math.abs(latitude).toFixed(5)}° ${latitude >= 0 ? "N" : "S"}, ${Math.abs(
    longitude,
  ).toFixed(5)}° ${longitude >= 0 ? "E" : "W"}`;
}

function formatOffset(hours: number): string {
  const sign = hours >= 0 ? "+" : "−";
  const total = Math.round(Math.abs(hours) * 60);
  return `UTC${sign}${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function wrapLongitude(value: number): number {
  return ((((value + 180) % 360) + 360) % 360) - 180;
}

function ClickToPick({
  onPick,
  disabled,
}: {
  onPick: (point: Pending) => void;
  disabled: boolean;
}) {
  useMapEvents({
    click(event) {
      if (disabled) return;
      onPick({
        latitude: event.latlng.lat,
        longitude: wrapLongitude(event.latlng.lng),
        name: null,
      });
    },
  });
  return null;
}

function Crosshair() {
  return (
    <svg
      viewBox="0 0 24 24"
      width={15}
      height={15}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx={12} cy={12} r={6.5} />
      <circle cx={12} cy={12} r={1.6} fill="currentColor" stroke="none" />
      <path d="M12 1.5v3M12 19.5v3M1.5 12h3M19.5 12h3" />
    </svg>
  );
}

function FlyTo({ flight }: { flight: Flight | null }) {
  const map = useMap();
  useEffect(() => {
    if (flight) map.flyTo(flight.point, flight.zoom, { duration: 0.8 });
  }, [flight, map]);
  return null;
}

function SiteCard({
  detail,
  loading,
  error,
}: {
  detail: SiteDetail | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (error) return <Failure error={error} />;
  if (loading || !detail) return <Loading label="loading the selected site…" />;
  const { site, summary, provenance } = detail;
  const ground = site.ground?.monthly_c ?? null;
  const standIn =
    detail.substituted_km != null &&
    detail.requested_latitude_deg != null &&
    detail.requested_longitude_deg != null;
  return (
    <div className="panel space-y-4 p-4 text-sm">
      {standIn && (
        <div className="rounded-lg border border-[var(--color-diesel)] bg-[var(--color-diesel)]/10 p-3">
          <p className="text-[12px] font-semibold text-[var(--color-danger-ink)]">
            This is not the point you clicked
          </p>
          <p className="tnum mt-1 text-[12px] leading-relaxed text-[var(--color-ink-dim)]">
            You clicked{" "}
            {formatCoordinate(detail.requested_latitude_deg!, detail.requested_longitude_deg!)}
            . That point could not be built, so everything below describes {site.name},{" "}
            {num(detail.substituted_km, 0)} km away. Its weather, skyline and ground belong
            to that place, so no result here is a result for your point.
          </p>
        </div>
      )}
      <div>
        <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-ink-faint)]">
          {standIn ? "Standing in for your point" : "Selected site"}
        </p>
        <p className="mt-0.5 text-base font-semibold leading-snug text-[var(--color-ink)]">
          {site.name}
        </p>
        <p className="tnum mt-1 text-[12px] text-[var(--color-ink-dim)]">
          {formatCoordinate(site.latitude_deg, site.longitude_deg)}
        </p>
        <p className="tnum text-[12px] text-[var(--color-ink-dim)]">
          {num(site.elevation_m, 0)} m · {formatOffset(site.time_zone_h)} standard time
        </p>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-3 text-[12px]">
        <div>
          <dt className="text-[var(--color-ink-faint)]">Climate</dt>
          <dd className="font-medium">{summary.climate_objective.replace(/_/g, " ")}</dd>
        </div>
        <div>
          <dt className="text-[var(--color-ink-faint)]">Results lead with</dt>
          <dd className="font-medium">{summary.headline_metric.replace(/_/g, " ")}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-[var(--color-ink-faint)]">Weather year</dt>
          <dd>
            {provenance
              ? `${provenance.weather_year} reanalysis, nearest grid point ${num(provenance.grid_distance_km, 1)} km away`
              : `Stored station file ${summary.weather_file}`}
          </dd>
        </div>
        {provenance && (
          <>
            <div>
              <dt className="text-[var(--color-ink-faint)]">Degree days, 18 °C base</dt>
              <dd className="tnum">
                {num(provenance.heating_degree_days, 0)} heating ·{" "}
                {num(provenance.cooling_degree_days, 0)} cooling
              </dd>
            </div>
            <div>
              <dt className="text-[var(--color-ink-faint)]">Monthly mean air</dt>
              <dd className="tnum">
                {num(provenance.coldest_month_mean_c, 1)} to{" "}
                {num(provenance.warmest_month_mean_c, 1)} °C
              </dd>
            </div>
          </>
        )}
        {ground && ground.length > 0 && (
          <div>
            <dt className="text-[var(--color-ink-faint)]">
              Ground at {num(site.ground?.depth_m, 1)} m
            </dt>
            <dd className="tnum">
              {num(Math.min(...ground), 1)} to {num(Math.max(...ground), 1)} °C
            </dd>
          </div>
        )}
      </dl>

      {site.horizon ? (
        <HorizonChart
          azimuths={[...site.horizon.azimuths_deg]}
          blocked={[...site.horizon.blocked_altitude_deg]}
        />
      ) : (
        <p className="text-[12px] text-[var(--color-ink-faint)]">
          No horizon profile is attached, so a flat skyline is assumed.
        </p>
      )}

      {(detail.notes ?? []).length > 0 && (
        <ul className="space-y-1.5 text-[11px] leading-relaxed text-[var(--color-ink-faint)]">
          {(detail.notes ?? []).map((note) => (
            <li key={note}>· {note}</li>
          ))}
        </ul>
      )}
      <Warnings warnings={detail.warnings ?? []} />
    </div>
  );
}

export function LocationPicker({
  meta,
  siteId,
  onSelect,
  disabled = false,
}: {
  meta: MetaResponse | undefined;
  siteId: string | null;
  onSelect: (siteId: string) => void;
  disabled?: boolean;
}) {
  const client = useQueryClient();
  const [pending, setPending] = useState<Pending | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [latitudeText, setLatitudeText] = useState("");
  const [longitudeText, setLongitudeText] = useState("");
  const [flight, setFlight] = useState<Flight | null>(null);

  const sites = meta?.sites ?? [];
  const selected = sites.find((item) => item.id === siteId) ?? null;

  const detail = useQuery({
    queryKey: ["site", siteId],
    queryFn: () => api.site(siteId!),
    enabled: Boolean(siteId),
    staleTime: Infinity,
  });
  const places = useMutation({ mutationFn: (text: string) => api.places(text) });
  const resolve = useMutation({
    mutationFn: (point: Pending) =>
      api.resolveSite({
        latitude_deg: point.latitude,
        longitude_deg: point.longitude,
        name: point.name,
      }),
    onSuccess: async (result) => {
      client.setQueryData(["site", result.site.id], result);
      await client.invalidateQueries({ queryKey: ["meta"] });
      onSelect(result.site.id);
      setPending(null);
    },
  });

  const locate = () => {
    setLocationError(null);
    if (!("geolocation" in navigator)) {
      setLocationError("This browser does not offer a location.");
      return;
    }
    if (!window.isSecureContext) {
      setLocationError(
        "Browsers only share a location over https or on localhost. Click the spot on "
        + "the map instead.",
      );
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocating(false);
        const { latitude, longitude, accuracy } = position.coords;
        setLocationError(
          accuracy > 2000
            ? `The device placed you to within about ${(accuracy / 1000).toFixed(1)} km, `
              + "which is coarse for a site. Drag the map and click the exact spot if it "
              + "matters."
            : null,
        );
        pick({ latitude, longitude, name: null }, 13);
      },
      (failure) => {
        setLocating(false);
        setLocationError(
          failure.code === failure.PERMISSION_DENIED
            ? "This browser refused to share a location. Allow it in the site settings, "
              + "or click the spot on the map."
            : failure.code === failure.POSITION_UNAVAILABLE
              ? "The device could not work out where it is. Click the spot on the map "
                + "instead."
              : "Finding you took too long. Click the spot on the map instead.",
        );
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 },
    );
  };

  const pick = (point: Pending, zoom?: number) => {
    setPending(point);
    setLatitudeText(point.latitude.toFixed(5));
    setLongitudeText(point.longitude.toFixed(5));
    resolve.reset();
    if (zoom !== undefined) {
      setFlight({ point: [point.latitude, point.longitude], zoom, key: Date.now() });
    }
  };

  const typedLatitude = Number(latitudeText);
  const typedLongitude = Number(longitudeText);
  const typedValid =
    latitudeText.trim() !== "" &&
    longitudeText.trim() !== "" &&
    Number.isFinite(typedLatitude) &&
    Number.isFinite(typedLongitude) &&
    Math.abs(typedLatitude) <= 84 &&
    Math.abs(typedLongitude) <= 180;

  const start: LatLngTuple = selected
    ? [selected.latitude_deg, selected.longitude_deg]
    : LEH;

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-3">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (query.trim().length >= 2) places.mutate(query.trim());
          }}
          className="flex gap-2"
        >
          <input
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search a place: Leh, Kargil, Diskit, Tangtse…"
            aria-label="Search for a place"
            className="min-w-0 flex-1 px-3 py-2 text-sm"
          />
          <button
            type="submit"
            disabled={places.isPending || query.trim().length < 2}
            className="rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] px-4 py-2 text-sm font-medium text-[var(--color-ink)] transition hover:border-[var(--color-solar)] disabled:opacity-40"
          >
            {places.isPending ? "Searching…" : "Search"}
          </button>
        </form>
        {places.isError && <Failure error={places.error} />}
        {places.data &&
          (places.data.length === 0 ? (
            <p className="text-[12px] text-[var(--color-ink-faint)]">
              The place index knows settlement names, not valleys, passes or postal
              addresses, and it is thin over Ladakh. Try the nearest village by name, or
              click the exact spot on the map, which always works and is more precise
              anyway.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {places.data.map((place) => (
                <li key={`${place.latitude_deg},${place.longitude_deg}`}>
                  <button
                    type="button"
                    onClick={() =>
                      pick(
                        {
                          latitude: place.latitude_deg,
                          longitude: place.longitude_deg,
                          name: place.name,
                        },
                        12,
                      )
                    }
                    className="rounded-full border border-[var(--color-line)] bg-[var(--color-night-2)] px-3 py-1 text-[12px] text-[var(--color-ink-dim)] transition hover:border-[var(--color-solar)] hover:text-[var(--color-ink)]"
                  >
                    {place.name}
                    <span className="text-[var(--color-ink-faint)]">
                      {[place.region, place.country].filter(Boolean).length > 0
                        ? ` · ${[place.region, place.country].filter(Boolean).join(", ")}`
                        : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ))}

        <div className="relative z-0 h-[480px] overflow-hidden rounded-xl border border-[var(--color-line)] shadow-sm">
          <MapContainer
            center={start}
            zoom={10}
            minZoom={2}
            worldCopyJump
            scrollWheelZoom
            className="h-full w-full"
          >
            <LayersControl position="topright">
              <LayersControl.BaseLayer checked name="Terrain">
                <TileLayer
                  className="map-drawn"
                  url="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png"
                  subdomains={["a", "b", "c"]}
                  maxZoom={17}
                  attribution='Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM | Style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)'
                />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Streets">
                <TileLayer
                  className="map-drawn"
                  url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                  maxZoom={19}
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
                />
              </LayersControl.BaseLayer>
              <LayersControl.BaseLayer name="Satellite">
                <TileLayer
                  url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
                  maxZoom={19}
                  attribution="Tiles &copy; Esri, Maxar, Earthstar Geographics and the GIS User Community"
                />
              </LayersControl.BaseLayer>
            </LayersControl>
            <ClickToPick onPick={(point) => pick(point)} disabled={disabled || resolve.isPending} />
            <FlyTo flight={flight} />
            {sites.map((item) => {
              const active = item.id === siteId;
              return (
                <CircleMarker
                  key={item.id}
                  center={[item.latitude_deg, item.longitude_deg]}
                  radius={active ? 9 : 6}
                  pathOptions={{
                    color: active ? "#047857" : "#1d4ed8",
                    fillColor: active ? "#10b981" : "#3b82f6",
                    fillOpacity: 0.9,
                    weight: 2,
                  }}
                  eventHandlers={{
                    click: () => {
                      if (disabled) return;
                      setPending(null);
                      onSelect(item.id);
                    },
                  }}
                >
                  <Tooltip>
                    {item.name} · {num(item.elevation_m, 0)} m
                  </Tooltip>
                </CircleMarker>
              );
            })}
            {pending && (
              <CircleMarker
                center={[pending.latitude, pending.longitude]}
                radius={8}
                pathOptions={{ color: "#c2410c", fillColor: "#fb923c", fillOpacity: 0.95, weight: 2 }}
              >
                <Tooltip permanent direction="top" offset={[0, -8]}>
                  {pending.name ?? "Picked point"}
                </Tooltip>
              </CircleMarker>
            )}
          </MapContainer>
        </div>

        <p className="text-[12px] leading-relaxed text-[var(--color-ink-faint)]">
          Click the exact spot, search a place, or type coordinates. Blue dots are sites already
          built and green is the one selected. The map pictures come from the internet and only
          help you find the place; the simulation never reads them.
        </p>

        <div className="flex flex-wrap items-end gap-3">
          <div className="w-40">
            <Field label="Latitude">
              <input
                type="number"
                step="0.00001"
                min={-84}
                max={84}
                value={latitudeText}
                onChange={(event) => setLatitudeText(event.target.value)}
                className="tnum w-full px-3 py-2 text-sm"
              />
            </Field>
          </div>
          <div className="w-40">
            <Field label="Longitude">
              <input
                type="number"
                step="0.00001"
                min={-180}
                max={180}
                value={longitudeText}
                onChange={(event) => setLongitudeText(event.target.value)}
                className="tnum w-full px-3 py-2 text-sm"
              />
            </Field>
          </div>
          <button
            type="button"
            disabled={!typedValid}
            onClick={() =>
              pick({ latitude: typedLatitude, longitude: typedLongitude, name: null }, 13)
            }
            className="rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] px-4 py-2 text-sm text-[var(--color-ink)] transition hover:border-[var(--color-solar)] disabled:opacity-40"
          >
            Show on map
          </button>
          <button
            type="button"
            disabled={locating}
            onClick={locate}
            title="Ask this browser where you are and drop the pin there. The reading comes from the device, not from the map."
            className="flex items-center gap-2 rounded-lg border border-[var(--color-line)] bg-[var(--color-night-2)] px-4 py-2 text-sm text-[var(--color-ink)] transition hover:border-[var(--color-solar)] disabled:opacity-40"
          >
            <Crosshair />
            {locating ? "Finding you…" : "Where I am"}
          </button>
        </div>

        {locationError && (
          <p className="text-[12px] text-[var(--color-danger-ink)]">{locationError}</p>
        )}

        {pending && (
          <div className="space-y-2 rounded-xl border border-[var(--color-pick-line)] bg-[var(--color-pick-bg)] p-4 text-sm">
            <p>
              <span className="font-medium text-[var(--color-ink)]">
                {pending.name ?? "Picked point"}
              </span>{" "}
              <span className="tnum text-[var(--color-ink-dim)]">
                at {formatCoordinate(pending.latitude, pending.longitude)}
              </span>
            </p>
            <p className="text-[12px] leading-relaxed text-[var(--color-ink-dim)]">
              Using this point fetches its elevation, a 36 direction horizon, a 10 km terrain
              square and one real year of hourly weather for exactly this spot. It takes about
              half a minute and needs the internet once; afterwards the site works offline.
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              <button
                type="button"
                disabled={disabled || resolve.isPending}
                onClick={() => resolve.mutate(pending)}
                className="rounded-lg bg-[var(--color-solar)] px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:shadow-md disabled:opacity-40"
              >
                {resolve.isPending ? "Building the site…" : "Use this point"}
              </button>
              <button
                type="button"
                disabled={resolve.isPending}
                onClick={() => {
                  setPending(null);
                  resolve.reset();
                }}
                className="rounded-lg px-3 py-2 text-sm text-[var(--color-ink-faint)] hover:text-[var(--color-ink)] disabled:opacity-40"
              >
                Cancel
              </button>
            </div>
            {resolve.isPending && (
              <Loading label="fetching elevation tiles, the horizon and the weather year…" />
            )}
            {resolve.isError && <Failure error={resolve.error} />}
          </div>
        )}
      </div>

      <aside className="lg:sticky lg:top-24 lg:self-start">
        {siteId ? (
          <SiteCard
            detail={detail.data}
            loading={detail.isPending}
            error={detail.error}
          />
        ) : (
          <p className="text-sm text-[var(--color-ink-faint)]">No site selected yet.</p>
        )}
      </aside>
    </div>
  );
}
