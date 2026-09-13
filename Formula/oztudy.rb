class Oztudy < Formula
  include Language::Python::Virtualenv

  desc "Daily academic planner for IB MYP (Flask desktop app)"
  homepage "https://github.com/OzNova/Oztudy"
  url "https://github.com/OzNova/Oztudy/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "a43e443276ace31b376eefeb189def7b95c1c2c4a6ce60b745964c05c51916c6"
  license "MIT"

  depends_on "python@3.13"

  def install
    venv = virtualenv_create(libexec/"venv", "python3")
    # Runtime deps mirror Planner/requirements.txt. Fetched from PyPI at
    # install time; flask is pinned to the tested release.
    venv.pip_install ["flask==3.0.3", "psutil>=5.9"]
    libexec.install "Planner"
    (bin/"oztudy").write <<~EOS
      #!/bin/bash
      export OZTUDY_DATA_DIR="${OZTUDY_DATA_DIR:-$HOME/.oztudy}"
      exec "#{libexec}/venv/bin/python" "#{libexec}/Planner/run_desktop.py" "$@"
    EOS
    (bin/"oztudy").chmod 0755
  end

  def caveats
    <<~EOS
      Oztudy stores your plans and statistics in ~/.oztudy
      (override with the OZTUDY_DATA_DIR environment variable).
      Launch the desktop app with:
        oztudy
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/oztudy --version")
  end
end
