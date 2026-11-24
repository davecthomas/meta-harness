# The console's Octofusion addresses

## Why

The console becomes Octofusion AI (`octofusion-rebrand.md`), and its address should carry the name. This is infrastructure, in two releases with a step between them that only a person can take, so it is done by hand and not planned as a goal's tasks.

## What we want

The console is served by a Firebase Hosting site, and a site's name is its address: `https://<site>.web.app`. A Firebase project can hold several sites, so the console gets two more beside the one it has:

- `octofusion-ai.web.app`, the address people are given;
- `octofusion.web.app`, which redirects to the first, path and query kept.

The address the console has today redirects to `octofusion-ai.web.app` the same way, so old links and bookmarks keep working. One address serves the console and the others redirect because a browser keeps a sign-in session for one address only: three live copies would mean three separate sign-ins.

Both names answered "Site Not Found" on 2026-11-22, which is what a free name answers. Firebase confirms a name only when the site is created, and names are unique across all of Firebase. If either is taken, the platform change stops and says so; nothing else in this spec waits on it.

What changes, in two releases so that nobody is locked out between them:

1. The platform stack creates the two sites, and the release stack serves the console at `octofusion-ai.web.app` as well as at the address it has today. The edge takes requests that change something only from the console's own address, `MH_CONSOLE_ORIGIN`. That setting becomes a list, and holds both addresses for now.
2. Google sign-in accepts a page only from an address its web client lists. A person adds `https://octofusion-ai.web.app` to the client's authorized JavaScript origins in the Google Cloud console, and signs in there to prove it. `deploy/README.md` gains that step.
3. A second release turns the old address and `octofusion.web.app` into redirects, takes the old address out of `MH_CONSOLE_ORIGIN`, and moves the links the control plane writes to the console, in pull requests and issues, to the new address.

The site the deployment has today is kept as a site throughout: the platform guard refuses a plan that deletes or replaces it.

The site names are settings in `deploy/environment.json`, as the first one is, and the deploy checks them at startup.

## Where it stands

Both releases are done (2026-11-23). The platform stack holds both sites (`console_home_site`, `console_redirect_sites`). `octofusion-ai.web.app` serves the console. `octofusion.web.app` and the deployment's first address answer every path with a 301 to it, path and query kept. `MH_CONSOLE_ORIGIN` holds the new address alone. The web client's authorized JavaScript origins list the new address. The sites' names are platform variables with defaults, not `deploy/environment.json` settings, so the deploy needs no new secret; Terraform checks each name.

## How we will know it works

- `octofusion-ai.web.app` serves the console and signs a person in.
- `octofusion.web.app` and the old address land on it with their path kept.
- Nobody is locked out between the two releases.

## Constraints

- An infrastructure change is a pull request that the deploy applies. It deletes and replaces nothing the platform guard protects.
- A new Google Cloud role the deploy needs goes into the platform stack in a pull request before the one that uses it.
- Every new or changed setting is checked at service startup.

## The next name: Muxxy

On 2026-11-23 the owner chose Muxxy as the platform's name, after turning down Octospawn and Muxtopus. Its Firebase site, `muxxy`, was checked creatable with Firebase's create call (`validateOnly`), and the platform stack now holds it (`console_next_site`). It serves the console beside `octofusion-ai.web.app`, and `MH_CONSOLE_ORIGIN` lists both. The web client lists `https://muxxy.web.app`, and on 2026-11-24 it became the home: `octofusion-ai.web.app`, `octofusion.web.app` and the deployment's first address answer every path with a 301 to it, path and query kept. The sites stay as the platform stack made them; the release stack says what each serves.
