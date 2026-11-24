// Created by the meta-harness in run t-0d024506d6dd:run-1 on harness:pi-careful/claude:claude-opus-5-5.

/// <reference types="vite/client" />

import { describe, expect, test } from "vitest";
import html from "../../index.html?raw";

// The files in public/, listed without loading them, and the manifest's text. `npm run brand` writes both.
const publicFiles = new Set(
  Object.keys(import.meta.glob("../../public/*")).map((path) => path.replace("../../public/", "")),
);
const manifestText = Object.values(
  import.meta.glob<string>("../../public/site.webmanifest", { query: "?raw", import: "default", eager: true }),
)[0];

interface Manifest {
  name: string;
  short_name: string;
  background_color: string;
  icons: { src: string; sizes: string; type: string; purpose?: string }[];
}

const doc = new DOMParser().parseFromString(html, "text/html");
const manifest = JSON.parse(manifestText ?? "null") as Manifest | null;

/** A root-relative address such as /favicon.svg as the name of a file in public/; other addresses stay whole. */
function publicName(href: string): string {
  return /^\/[^/]+$/.test(href) ? href.slice(1) : href;
}

const htmlLinks = Array.from(doc.querySelectorAll("link[rel~='icon'], link[rel='apple-touch-icon'], link[rel='manifest']"));
const htmlNames = htmlLinks.map((link) => publicName(link.getAttribute("href") ?? ""));

describe("index.html", () => {
  test("titles the tab Muxxy AI and sets the theme color", () => {
    expect(doc.title).toBe("Muxxy AI");
    expect(doc.querySelector("meta[name='theme-color']")?.getAttribute("content")).toBe("#670fdc");
  });

  test("links the icons and the manifest", () => {
    for (const name of ["favicon.svg", "favicon.ico", "favicon-32.png", "apple-touch-icon.png", "site.webmanifest"]) {
      expect(htmlNames).toContain(name);
    }
  });

  test.each(htmlNames)("names %s, which is in public/", (name) => {
    expect(publicFiles).toContain(name);
  });
});

describe("site.webmanifest", () => {
  test("is in public/ and names the app Muxxy AI on near-black", () => {
    expect(manifest).not.toBeNull();
    expect(manifest?.name).toBe("Muxxy AI");
    expect(manifest?.short_name).toBe("Muxxy AI");
    expect(manifest?.background_color).toBe("#0a0a14");
  });

  test("lists a 192 and a 512 pixel icon and a maskable one", () => {
    const icons = manifest?.icons ?? [];
    expect(icons.map((icon) => icon.sizes)).toEqual(expect.arrayContaining(["192x192", "512x512"]));
    expect(icons.some((icon) => icon.purpose === "maskable")).toBe(true);
  });

  test.each((manifest?.icons ?? []).map((icon) => icon.src))("names %s, which is in public/", (src) => {
    expect(publicFiles).toContain(publicName(src));
  });
});
