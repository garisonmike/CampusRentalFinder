#!/usr/bin/env node
/**
 * The performance budget, enforced.
 *
 * The user is a student on a mid-range Android, on mobile data, often on
 * campus wifi that barely works. Every kilobyte of initial JavaScript is time
 * they wait before a listing appears, and on a slow CPU it is parse time as
 * well as download time — which is the part a fast laptop never shows you.
 *
 * **A budget that is not enforced is decoration.** `docs/OPERATIONS.md`
 * records four bugs of the form "the check and the checked thing were
 * configured in two places and the wrong one won silently"; a budget written
 * in a README is the same shape with no second place at all. So this runs in
 * CI, reads what was actually built, and exits non-zero.
 *
 * Measured gzipped, because that is what crosses the network. Raw size is
 * recorded too, since it is what the phone has to parse.
 */

import { gzipSync } from "node:zlib";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const DIST = new URL("../dist/assets/", import.meta.url).pathname;

/**
 * Gzipped kilobyte ceilings.
 *
 * Set from a real measurement with deliberate headroom, not from a round
 * number: the entry chunk was 113.6 kB when this was written, and 130 gives
 * room for the remaining pages without leaving space for a stray chart
 * library. Raising one of these is a decision that belongs in a commit
 * message, which is the point of failing rather than warning.
 */
const BUDGET_KB = {
  /** The shared chunk every page pays for, before any route loads. */
  entryJs: 130,
  /** All CSS. One file, because Tailwind emits one. */
  css: 12,
  /** Any single lazily-loaded route. A route that outgrows this is doing
   *  something a route should not, or needs its own split. */
  routeJs: 40,
};

function kb(bytes) {
  return Math.round((bytes / 1024) * 100) / 100;
}

function gzippedKb(path) {
  return kb(gzipSync(readFileSync(path)).length);
}

function measure() {
  let names;
  try {
    names = readdirSync(DIST);
  } catch {
    // A missing dist is a build that did not run. Reported as such rather
    // than as a stack trace, because the person reading CI needs to know
    // which step to fix.
    console.error(
      "dist/assets does not exist. Run `npm run build` before the budget " +
        "check -- a budget that never measured anything is not a passing " +
        "budget.",
    );
    process.exit(2);
  }

  const files = names.filter(
    (name) => !name.endsWith(".map") && statSync(join(DIST, name)).isFile(),
  );

  const entries = [];
  const routes = [];
  const styles = [];

  for (const name of files) {
    const path = join(DIST, name);
    const record = { name, gzip: gzippedKb(path), raw: kb(statSync(path).size) };

    if (name.endsWith(".css")) styles.push(record);
    // Vite names the entry chunk `index-<hash>.js`; every route chunk is
    // named after its component. Matching on the entry name rather than on
    // size, so a route that grows past the entry does not silently swap roles.
    else if (/^index-.*\.js$/.test(name)) entries.push(record);
    else if (name.endsWith(".js")) routes.push(record);
  }

  return { entries, routes, styles };
}

function report(label, records, budget) {
  const failures = [];

  for (const record of records) {
    const status = record.gzip > budget ? "OVER" : "ok";
    console.log(
      `  ${status.padEnd(4)} ${record.name.padEnd(38)} ` +
        `${String(record.gzip).padStart(7)} kB gzip  (${record.raw} kB raw, budget ${budget})`,
    );
    if (record.gzip > budget) failures.push(record);
  }

  if (records.length === 0) console.log("  (none)");
  return failures;
}

const { entries, routes, styles } = measure();

if (entries.length === 0) {
  console.error(
    "No entry chunk found in dist/assets. Run `npm run build` first -- a " +
      "budget check that silently passes on an empty directory is worse than " +
      "no check.",
  );
  process.exit(2);
}

console.log("Initial JavaScript (every page pays this):");
const entryFailures = report("entry", entries, BUDGET_KB.entryJs);

console.log("\nStylesheets:");
const cssFailures = report("css", styles, BUDGET_KB.css);

console.log("\nRoute chunks (loaded on navigation):");
const routeFailures = report("route", routes, BUDGET_KB.routeJs);

const failures = [...entryFailures, ...cssFailures, ...routeFailures];

const initialKb =
  entries.reduce((total, record) => total + record.gzip, 0) +
  styles.reduce((total, record) => total + record.gzip, 0);
console.log(`\nInitial payload (entry JS + CSS): ${Math.round(initialKb * 100) / 100} kB gzip`);

if (failures.length > 0) {
  console.error(
    `\nOver budget: ${failures.map((record) => record.name).join(", ")}\n\n` +
      "Either find the weight, or raise the budget in " +
      "scripts/check-bundle-budget.mjs with a commit message saying why. " +
      "The student on a mid-range Android does not get a vote, so somebody " +
      "has to take it deliberately.",
  );
  process.exit(1);
}

console.log("\nWithin budget.");
