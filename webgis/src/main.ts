import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "./styles.css";

const mapElement = document.querySelector<HTMLDivElement>("#map");
if (!mapElement) {
  throw new Error("Map container #map was not found.");
}

const map = L.map(mapElement, {
  center: [41.1579, -8.6291],
  zoom: 13,
  zoomControl: false,
});

L.control.zoom({ position: "bottomright" }).addTo(map);

const streetMap = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
});

const satellite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    attribution: "Tiles &copy; Esri and its contributors",
    maxZoom: 19,
  },
);

const wmsUrl = "/geoserver/sod/wms";
const wmsOptions: L.WMSOptions = {
  format: "image/png",
  transparent: true,
  version: "1.1.1",
  uppercase: true,
};

const buildings = L.tileLayer.wms(wmsUrl, {
  ...wmsOptions,
  layers: "sod:osm_buildings",
  opacity: 0.48,
});

const predictedEntrances = L.markerClusterGroup({
  chunkedLoading: true,
  maxClusterRadius: 48,
  disableClusteringAtZoom: 17,
  spiderfyOnMaxZoom: true,
  showCoverageOnHover: false,
  iconCreateFunction: (cluster) => {
    const count = cluster.getChildCount();
    const size = count < 10 ? 40 : count < 100 ? 46 : 52;
    return L.divIcon({
      className: "entrance-cluster-icon",
      html: `<span>${count}</span>`,
      iconSize: L.point(size, size),
    });
  },
});

const roads = L.tileLayer.wms(wmsUrl, {
  ...wmsOptions,
  layers: "sod:ctx_roads",
  opacity: 0.85,
});

streetMap.addTo(map);
buildings.addTo(map);
predictedEntrances.addTo(map);

const entranceCount = document.querySelector<HTMLElement>("#entrance-count");
const serviceStatus = document.querySelector<HTMLElement>("#service-status-label");
const numberFormat = new Intl.NumberFormat("en-GB");

const predictedEntranceIcon = L.divIcon({
  className: "predicted-entrance-icon",
  html: '<span class="predicted-entrance-marker" aria-hidden="true"></span>',
  iconSize: [36, 40],
  iconAnchor: [18, 38],
  popupAnchor: [0, -34],
});

const queryableLayers = [
  { label: "Road", layer: roads },
  { label: "Building", layer: buildings },
];

type GeoServerFeature = {
  properties?: Record<string, unknown>;
};

type FeatureInfoResponse = {
  features?: GeoServerFeature[];
};

function escapeHtml(value: unknown): string {
  return String(value).replace(/[&<>"']/g, (character) => {
    const entities: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character] ?? character;
  });
}

function featureTitle(label: string, properties: Record<string, unknown>): string {
  if (label === "Predicted entrance") {
    return String(properties.name || "Predicted entrance");
  }
  return String(properties.name || label);
}

function featureDetails(
  label: string,
  properties: Record<string, unknown>,
): Array<[string, unknown]> {
  if (label === "Predicted entrance") {
    const confidence = Number(properties.probability);
    const margin = Number(properties.margin);
    return [
      ["Building type", properties.building_type || properties.amenity],
      ["Model", properties.model],
      ["Confidence", Number.isFinite(confidence) ? `${(confidence * 100).toFixed(1)}%` : null],
      ["Confidence margin", Number.isFinite(margin) ? margin.toFixed(3) : null],
      ["Building ID", properties.building_id],
    ];
  }
  if (label === "Building") {
    return [
      ["Building type", properties.building_type],
      ["Amenity", properties.amenity],
      ["Address street", properties.addr_street],
      ["OSM ID", properties.osm_id],
    ];
  }
  return [
    ["Road type", properties.kind],
    ["OSM ID", properties.id],
  ];
}

function featureInfoUrl(
  layer: L.TileLayer.WMS,
  point: L.LatLng,
): string {
  const size = map.getSize();
  const pixel = map.latLngToContainerPoint(point);
  const layerName = String(layer.wmsParams.layers);
  const url = new URL(wmsUrl, window.location.href);
  url.search = new URLSearchParams({
    SERVICE: "WMS",
    VERSION: "1.1.1",
    REQUEST: "GetFeatureInfo",
    LAYERS: layerName,
    QUERY_LAYERS: layerName,
    STYLES: "",
    BBOX: map.getBounds().toBBoxString(),
    SRS: "EPSG:4326",
    WIDTH: String(size.x),
    HEIGHT: String(size.y),
    FORMAT: "image/png",
    INFO_FORMAT: "application/json",
    FEATURE_COUNT: "3",
    X: String(Math.round(pixel.x)),
    Y: String(Math.round(pixel.y)),
  }).toString();
  return url.toString();
}

function featurePopup(label: string, properties: Record<string, unknown>): string {
  const rows = featureDetails(label, properties)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(
      ([name, value]) =>
        `<div class="popup-row"><span>${escapeHtml(name)}</span><strong>${escapeHtml(value)}</strong></div>`,
    )
    .join("");
  return `<section class="feature-popup"><h3>${escapeHtml(featureTitle(label, properties))}</h3><p class="feature-kind">${escapeHtml(label)}</p>${rows}</section>`;
}

async function loadPredictedEntrances(): Promise<void> {
  const parameters = new URLSearchParams({
    service: "WFS",
    version: "1.0.0",
    request: "GetFeature",
    typeName: "sod:predicted_entrances",
    outputFormat: "application/json",
  });

  try {
    const response = await fetch(`/geoserver/sod/ows?${parameters.toString()}`);
    if (!response.ok) {
      throw new Error(`GeoServer returned HTTP ${response.status}`);
    }

    const collection = (await response.json()) as GeoJSON.GeoJsonObject;
    const markers = L.geoJSON(collection, {
      pointToLayer: (_feature, latlng) =>
        L.marker(latlng, {
          icon: predictedEntranceIcon,
          title: "Predicted entrance; click for details",
          bubblingMouseEvents: false,
        }),
      onEachFeature: (feature, marker) => {
        const properties = (feature.properties ?? {}) as Record<string, unknown>;
        marker.bindPopup(featurePopup("Predicted entrance", properties), {
          maxWidth: 320,
        });
      },
    });
    predictedEntrances.addLayers(markers.getLayers());

    if (entranceCount) {
      entranceCount.textContent = `${markers.getLayers().length.toLocaleString()} predictions`;
    }
    if (serviceStatus) {
      serviceStatus.textContent = "GeoServer";
    }
  } catch (error) {
    console.error("Could not load predicted entrances from GeoServer WFS:", error);
    if (entranceCount) {
      entranceCount.textContent = "Could not load predictions";
    }
    if (serviceStatus) {
      serviceStatus.textContent = "GeoServer connection issue";
    }
  }
}

void loadPredictedEntrances();

async function fetchFeatureCount(typeName: string): Promise<number> {
  const parameters = new URLSearchParams({
    service: "WFS",
    version: "2.0.0",
    request: "GetFeature",
    typeNames: `sod:${typeName}`,
    resultType: "hits",
  });
  const response = await fetch(`/geoserver/sod/ows?${parameters.toString()}`);
  if (!response.ok) {
    throw new Error(`GeoServer returned HTTP ${response.status} for ${typeName}`);
  }

  const document = new DOMParser().parseFromString(await response.text(), "application/xml");
  const count = document.documentElement.getAttribute("numberMatched");
  if (!count || count === "unknown" || !/^\d+$/.test(count)) {
    throw new Error(`GeoServer did not return a feature count for ${typeName}`);
  }
  return Number(count);
}

async function loadStatistics(): Promise<void> {
  const statistics = [
    ["stat-entrances", "predicted_entrances"],
    ["stat-buildings", "osm_buildings"],
    ["stat-roads", "ctx_roads"],
  ] as const;

  await Promise.all(
    statistics.map(async ([elementId, typeName]) => {
      const element = document.querySelector<HTMLElement>(`#${elementId}`);
      try {
        const count = await fetchFeatureCount(typeName);
        if (element) element.textContent = numberFormat.format(count);
      } catch (error) {
        console.error(`Could not load the ${typeName} statistic:`, error);
        if (element) element.textContent = "—";
        element?.setAttribute("aria-label", "Count unavailable");
      }
    }),
  );
}

void loadStatistics();

map.on("click", async (event) => {
  const visible = queryableLayers.filter(({ layer }) => map.hasLayer(layer));
  const results = await Promise.all(
    visible.map(async ({ label, layer }) => {
      try {
        const response = await fetch(featureInfoUrl(layer, event.latlng));
        if (!response.ok) return [];
        const data = (await response.json()) as FeatureInfoResponse;
        return (data.features ?? [])
          .slice(0, 1)
          .map((feature) => ({ label, properties: feature.properties ?? {} }));
      } catch {
        return [];
      }
    }),
  );

  const matches = results.flat();
  if (matches.length === 0) {
    L.popup().setLatLng(event.latlng).setContent("No map features found here.").openOn(map);
    return;
  }

  const content = matches
    .map(({ label, properties }) => featurePopup(label, properties))
    .join('<hr class="popup-divider">');
  L.popup({ maxWidth: 320 }).setLatLng(event.latlng).setContent(content).openOn(map);
});

L.control
  .layers(
    {
      Streets: streetMap,
      Satellite: satellite,
    },
    {
      "Predicted entrances": predictedEntrances,
      "OSM buildings": buildings,
      "Road context": roads,
    },
    { collapsed: false, position: "topright" },
  )
  .addTo(map);
