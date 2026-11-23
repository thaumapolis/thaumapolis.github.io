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
      # A generated fontconfig config naming only the pinned font directories.
      # Written as a store path so it is identical wherever the shell is entered.
      fontsConfFor = pkgs:
        pkgs.writeText "thaumapolis-fonts.conf" ''
          <?xml version="1.0"?>
          <!DOCTYPE fontconfig SYSTEM "fonts.dtd">
          <fontconfig>
            <dir>${pkgs.liberation_ttf}/share/fonts/truetype</dir>
            <cachedir>/tmp/fontconfig-cache</cachedir>
            # Fontconfig has no built-in notion of which family answers a generic
            # request, so aliasing them explicitly. Without these the browser's
            # sans-serif and monospace both land on one face, which is exactly the
            # condition the goldens guard rejects. Liberation's families stand in
            # for Arial/Times/Courier as they conventionally do.
            <match target="pattern">
              <test name="family"><string>sans-serif</string></test>
              <edit name="family" mode="prepend" binding="strong"><string>Liberation Sans</string></edit>
            </match>
            <match target="pattern">
              <test name="family"><string>serif</string></test>
              <edit name="family" mode="prepend" binding="strong"><string>Liberation Serif</string></edit>
            </match>
            <match target="pattern">
              <test name="family"><string>monospace</string></test>
              <edit name="family" mode="prepend" binding="strong"><string>Liberation Mono</string></edit>
            </match>
          </fontconfig>
        '';

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
            # Fonts are part of the rendering environment, so they are declared
            # rather than inherited from the host. Without this, CI renders with
            # whatever the GitHub runner happens to ship and a local machine with
            # a different set can neither reproduce nor even legally compare a
            # baseline: the goldens guard refuses to paint when sans-serif and
            # monospace measure the same width. Liberation provides the
            # sans/serif/mono triad the guard checks for.
            pkgs.liberation_ttf
            pkgs.fontconfig
          ];

          env = {
            # ESM ignores NODE_PATH, so the driver is addressed by absolute path.
            #
            # The layout is top-level: playwright-driver realises as
            # <store>/index.mjs, <store>/lib/, <store>/browsers.json. An earlier
            # comment here claimed the opposite -- "its own top level has no
            # index.mjs at all", asserting lib/node_modules/playwright/index.mjs as
            # verified by listing the output. It was not verified, and the path it
            # produced does not exist, so every browser phase in CI died with
            # ERR_MODULE_NOT_FOUND while the job still deployed: pages.yml calls
            # generate.py, not check.sh, so nothing that gates publication ever ran
            # the goldens. The same package's shape is what ridgeline/flake.nix
            # already uses and its CI is green.
            PLAYWRIGHT_ENTRY = "${pkgs.playwright-driver}/index.mjs";
            PLAYWRIGHT_BROWSERS_PATH = "${pkgs.playwright-driver.browsers}";
            # fonttools needs a brotli extension to read/write WOFF2 and does not
            # declare it, so its site-packages go on PYTHONPATH by hand. Pillow
            # is in the shell already; listing it costs nothing and stops the
            # same variable being the reason a different tool goes missing.
            # A fontconfig file generated below points at the fonts declared above,
            # so local and CI resolve font-family the same way regardless of host.
            FONTCONFIG_FILE = "${fontsConfFor pkgs}";
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
