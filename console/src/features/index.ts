// Every feature, in navigation order. A new feature adds one line here and nothing else outside its folder.
import { feature as overview } from "./overview";
import { feature as goals } from "./goals";
import { feature as tasks } from "./tasks";
import { feature as performance } from "./performance";
import { feature as config } from "./config";
import { feature as admin } from "./admin";
import { feature as settings } from "./settings";
import type { Feature } from "./types";

export const FEATURES: Feature[] = [overview, goals, tasks, performance, config, admin, settings];
