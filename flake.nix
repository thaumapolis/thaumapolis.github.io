{
  description = "thaumapolis.github.io: the organisation front page, generated from the projects";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      # Goldens are painted and compared inside one browser process, so a
      # baseline only means the same pixels on the same browser build. Only
      # x86_64-linux is exercised by CI.
      systems = [ "x86_64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShellNoCC {
          packages = [
            pkgs.nodejs_22
            pkgs.playwright-driver
            pkgs.git
            pkgs.python3
            pkgs.python3Packages.fonttools
            pkgs.python3Packages.brotli
            # tools/make_thumbs.py resizes and encodes the card images. Only the
            # regeneration step needs it -- the committed .webp files are the
            # generator's input -- but it is in the shell so local and CI runs
            # of ./check.sh are the same command.
            pkgs.python3Packages.pillow
            pkgs.ruff
          ];

          env = {
            # ESM ignores NODE_PATH, so the driver is addressed by absolute path.
            #
            # The path is not "${pkgs.playwright-driver}/index.mjs": that package
            # installs the library under lib/node_modules/playwright/, and its own
            # top level has no index.mjs at all. This was verified by listing the
            # realised output rather than by copying the expression from another
            # project's flake, which is where the wrong shape came from.
            PLAYWRIGHT_ENTRY = "${pkgs.playwright-driver}/lib/node_modules/playwright/index.mjs";
            PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
            # fonttools needs a brotli extension to read/write WOFF2 and does not
            # declare it, so its site-packages go on PYTHONPATH by hand. Pillow
            # is in the shell already; listing it costs nothing and stops the
            # same variable being the reason a different tool goes missing.
            PYTHONPATH = builtins.concatStringsSep ":" [
              "${pkgs.python3Packages.brotli}/${pkgs.python3.sitePackages}"
              "${pkgs.python3Packages.pillow}/${pkgs.python3.sitePackages}"
            ];
          };

          shellHook = ''
            echo "thaumapolis.github.io: ./check.sh   (ruff, render, goldens)"
          '';
        };
      });
    };
}
