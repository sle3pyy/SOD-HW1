/** Provision both PostGIS stores and publish their spatial tables in GeoServer. */

declare const process: {
  env: Record<string, string | undefined>;
  exitCode?: number;
};

type JsonObject = Record<string, unknown>;

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Required environment variable ${name} is missing`);
  }
  return value;
}

const baseUrl = (process.env.GEOSERVER_URL ?? "http://geoserver:8080/geoserver").replace(
  /\/$/,
  "",
);
const adminUser = required("GEOSERVER_ADMIN_USER");
const adminPassword = required("GEOSERVER_ADMIN_PASSWORD");
const workspace = process.env.GEOSERVER_WORKSPACE ?? "sod";
const dataStores = [
  {
    name: process.env.GEOSERVER_PARTA_DATASTORE ?? "parta",
    schema: process.env.GEOSERVER_PARTA_SCHEMA ?? "parta",
  },
  {
    name: process.env.GEOSERVER_PARTB_DATASTORE ?? "partb",
    schema: process.env.GEOSERVER_PARTB_SCHEMA ?? "partb",
  },
];

async function request(
  method: string,
  path: string,
  payload?: JsonObject,
): Promise<Response> {
  const headers = new Headers({
    Accept: "application/json",
    Authorization: `Basic ${btoa(`${adminUser}:${adminPassword}`)}`,
  });
  if (payload) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(`${baseUrl}/rest${path}`, {
      method,
      headers,
      body: payload ? JSON.stringify(payload) : undefined,
      signal: AbortSignal.timeout(15_000),
    });
  } catch (error) {
    throw new Error(`Cannot reach GeoServer REST API: ${String(error)}`);
  }

  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    if (response.status === 404 && method === "GET") {
      return response;
    }
    throw new Error(
      `GeoServer REST ${method} ${path} failed (${response.status}): ${detail}`,
    );
  }
  return response;
}

async function getJson(path: string): Promise<JsonObject> {
  return (await request("GET", path)).json() as Promise<JsonObject>;
}

async function waitUntilReady(): Promise<void> {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    try {
      await request("GET", "/about/version.json");
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 3_000));
    }
  }
  throw new Error("GeoServer REST API did not become ready within 180 seconds");
}

async function ensureWorkspace(): Promise<void> {
  const path = `/workspaces/${workspace}.json`;
  const response = await request("GET", path);
  if (response.status === 404) {
    await request("POST", "/workspaces", { workspace: { name: workspace } });
    console.log(`Created GeoServer workspace ${workspace}.`);
  } else {
    console.log(`GeoServer workspace ${workspace} already exists.`);
  }
}

async function ensurePostgisStore(name: string, schema: string): Promise<void> {
  const parameters: Record<string, string> = {
    host: process.env.POSTGRES_HOST ?? "postgis",
    port: process.env.POSTGRES_PORT ?? "5432",
    database: required("POSTGRES_DB"),
    schema,
    user: required("POSTGRES_USER"),
    passwd: required("POSTGRES_PASSWORD"),
    dbtype: "postgis",
    "Expose primary keys": "true",
    "validate connections": "true",
  };
  const payload = {
    dataStore: {
      name,
      type: "PostGIS",
      enabled: true,
      connectionParameters: {
        entry: Object.entries(parameters).map(([key, value]) => ({
          "@key": key,
          $: value,
        })),
      },
    },
  };
  const collection = `/workspaces/${workspace}/datastores.json`;
  const resource = `/workspaces/${workspace}/datastores/${name}.json`;
  const response = await request("GET", resource);
  if (response.status === 404) {
    await request("POST", collection, payload);
    console.log(`Created PostGIS store ${workspace}:${name} for schema ${schema}.`);
  } else {
    await request("PUT", resource, payload);
    console.log(`Updated PostGIS store ${workspace}:${name} for schema ${schema}.`);
  }
}

async function publishAvailableLayers(name: string): Promise<void> {
  const path = `/workspaces/${workspace}/datastores/${name}/featuretypes`;
  const response = await getJson(`${path}.json?list=available_with_geom`);
  const list = response.list as { string?: string | string[] } | undefined;
  const available = list?.string;
  const names = typeof available === "string" ? [available] : (available ?? []);

  for (const layerName of names) {
    await request("POST", `${path}.json`, { featureType: { name: layerName } });
    console.log(`Published GeoServer layer ${workspace}:${layerName}.`);
  }
  if (names.length === 0) {
    console.log(`No unpublished spatial tables found in store ${name}.`);
  }
}

async function main(): Promise<void> {
  await waitUntilReady();
  await ensureWorkspace();
  for (const store of dataStores) {
    await ensurePostgisStore(store.name, store.schema);
    await publishAvailableLayers(store.name);
  }
}

main().catch((error: unknown) => {
  console.error(`GeoServer provisioning failed: ${String(error)}`);
  process.exitCode = 1;
});
