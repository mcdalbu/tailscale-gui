Name:           tailscale-gui
Version:        1.0.0
Release:        1%{?dist}
Summary:        Interface gráfica GTK4 para o Tailscale

License:        MIT
URL:            https://github.com/mcda/tailscale-gui
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3

Requires:       python3
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       tailscale

%description
Interface gráfica nativa GTK4/libadwaita para controlar o Tailscale.
Permite conectar, desconectar, visualizar peers, configurar exit node
e ajustar opções como Accept Routes, SSH e Shields Up.

%prep
%setup -q

%install
mkdir -p %{buildroot}%{_bindir}
mkdir -p %{buildroot}%{_datadir}/applications
mkdir -p %{buildroot}%{_datadir}/icons/hicolor/256x256/apps

install -m 755 tailscale-gui.py   %{buildroot}%{_bindir}/tailscale-gui
install -m 644 tailscale-gui.desktop %{buildroot}%{_datadir}/applications/tailscale-gui.desktop
install -m 644 tailscale-gui.png  %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/tailscale-gui.png

%files
%{_bindir}/tailscale-gui
%{_datadir}/applications/tailscale-gui.desktop
%{_datadir}/icons/hicolor/256x256/apps/tailscale-gui.png

%post
gtk-update-icon-cache -f -t %{_datadir}/icons/hicolor &>/dev/null || :

%changelog
* Sat Apr 19 2026 mcda - 1.0.0-1
- Versão inicial com GTK4/libadwaita
- Status, lista de peers, exit node, toggles de configuração
