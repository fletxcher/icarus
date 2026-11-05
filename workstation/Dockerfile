# The solver image the Icarus work station runs its jobs in: XFOIL, gmsh, OpenFOAM, CalculiX,
# ParaView, ffmpeg and the Python environment for meshing and CAD. Built by setup.sh; the image
# is tagged with a hash of this file, so a changed Dockerfile means a new image.
FROM ubuntu:24.04

# OpenFOAM release from the ESI apt repository (dl.openfoam.com).
ARG OPENFOAM_PACKAGE=openfoam2512
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg lsb-release python3 python3-venv \
      xfoil xvfb xauth xfonts-base xfonts-75dpi xfonts-100dpi \
      gmsh calculix-ccx paraview ffmpeg \
 && curl -fsSL https://dl.openfoam.com/add-debian-repo.sh | bash \
 && apt-get update \
 && apt-get install -y --no-install-recommends "$OPENFOAM_PACKAGE" \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/icarus/venv \
 && /opt/icarus/venv/bin/pip install --no-cache-dir numpy scipy pyvista pytetwild build123d

ENV PATH=/opt/icarus/venv/bin:$PATH \
    ICARUS_VENV=/opt/icarus/venv
