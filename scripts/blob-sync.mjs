import { access, mkdir, readFile, readdir, rename, writeFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";

const args = parseArgs(process.argv.slice(2));
const queueRoot = args.queueRoot;
const uploadedRoot = join(queueRoot, "uploaded");

if (!args.dryRun && !process.env.BLOB_READ_WRITE_TOKEN) {
  console.error("BLOB_READ_WRITE_TOKEN is required to sync queued raw objects to Vercel Blob.");
  process.exit(2);
}

// Keep validation-only dry runs dependency-free so the Python test job and
// local operators can audit a queue without installing the upload client.
const putBlob = args.dryRun ? null : (await import("@vercel/blob")).put;

await mkdir(uploadedRoot, { recursive: true });

const queueFiles = (await readdir(queueRoot, { withFileTypes: true }))
  .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
  .filter((entry) => !entry.name.endsWith(".result.json"))
  .map((entry) => join(queueRoot, entry.name));

const results = [];
const errors = [];
for (const queuePath of queueFiles) {
  const item = JSON.parse(await readFile(queuePath, "utf8"));
  const validationError = await validateQueueItem(item, queuePath);
  if (validationError) {
    errors.push(validationError);
    continue;
  }
  if (args.dryRun) {
    results.push({
      blob_key: item.blob_key,
      local_path: item.local_path,
      content_type: item.content_type,
      status: validationError ? "invalid" : "ready"
    });
    continue;
  }
  const payload = await readFile(item.local_path);
  const result = await putBlob(item.blob_key, payload, {
    access: "private",
    allowOverwrite: true,
    contentType: item.content_type,
    addRandomSuffix: false
  });
  const archivedPath = join(uploadedRoot, basename(queuePath));
  await mkdir(dirname(archivedPath), { recursive: true });
  await rename(queuePath, archivedPath);
  const resultPath = join(uploadedRoot, `${basename(queuePath)}.result.json`);
  await writeFile(
    resultPath,
    JSON.stringify(
      {
        ...item,
        blob: {
          url: result.url,
          downloadUrl: result.downloadUrl,
          pathname: result.pathname,
          contentType: result.contentType
        }
      },
      null,
      2
    )
  );
  results.push({ blob_key: item.blob_key, url: result.url });
}

const summary = {
  mode: args.dryRun ? "dry_run" : "upload",
  queue_root: queueRoot,
  scanned: queueFiles.length,
  uploaded: args.dryRun ? 0 : results.length,
  ready: args.dryRun ? results.filter((item) => item.status === "ready").length : undefined,
  errors,
  results
};
console.log(JSON.stringify(summary, null, 2));

if (errors.length && args.failMissing) {
  process.exit(3);
}

function parseArgs(argv) {
  const parsed = {
    queueRoot: "storage/blob_queue",
    dryRun: false,
    failMissing: true
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") {
      parsed.dryRun = true;
    } else if (arg === "--skip-missing") {
      parsed.failMissing = false;
    } else if (arg === "--queue-root") {
      parsed.queueRoot = argv[index + 1] ?? parsed.queueRoot;
      index += 1;
    } else if (!arg.startsWith("--")) {
      parsed.queueRoot = arg;
    } else {
      console.error(`Unknown argument: ${arg}`);
      process.exit(2);
    }
  }
  return parsed;
}

async function validateQueueItem(item, queuePath) {
  const missingFields = ["local_path", "blob_key", "content_type"].filter((field) => !item[field]);
  if (missingFields.length) {
    return {
      queue_path: queuePath,
      code: "invalid_manifest",
      detail: `missing fields: ${missingFields.join(", ")}`
    };
  }
  try {
    await access(item.local_path);
  } catch {
    return {
      queue_path: queuePath,
      code: "missing_local_path",
      local_path: item.local_path,
      blob_key: item.blob_key
    };
  }
  return null;
}
