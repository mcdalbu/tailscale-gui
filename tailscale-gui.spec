Name:           tailscale-gui
Version:        1.2.0
Release:        1%{?dist}
Summary:        Interface gráfica GTK4 para o Tailscale

License:        MIT
URL:            https://github.com/mcdalbu/tailscale-gui
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       gtk4 >= 4.12
Requires:       libadwaita >= 1.4
Requires:       tailscale
Requires:       polkit

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
mkdir -p %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
mkdir -p %{buildroot}%{_datadir}/tailscale-gui

install -m 755 tailscale-gui.py   %{buildroot}%{_bindir}/tailscale-gui
install -m 644 tailscale-gui.desktop %{buildroot}%{_datadir}/applications/io.tailscale.TailscaleGUI.desktop
install -m 644 tailscale-gui.png  %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/tailscale-gui.png
install -m 644 tailscale-gui.svg  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/tailscale-gui.svg
install -m 644 tailscale-gui-tray.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/tailscale-gui-tray.svg
install -m 644 tailscale_tray.py %{buildroot}%{_datadir}/tailscale-gui/tailscale_tray.py

%check
python3 -m py_compile tailscale-gui.py tailscale_tray.py

%files
%doc README.md
%{_bindir}/tailscale-gui
%{_datadir}/applications/io.tailscale.TailscaleGUI.desktop
%{_datadir}/icons/hicolor/256x256/apps/tailscale-gui.png
%{_datadir}/icons/hicolor/scalable/apps/tailscale-gui.svg
%{_datadir}/icons/hicolor/scalable/apps/tailscale-gui-tray.svg
%{_datadir}/tailscale-gui/

%post
gtk-update-icon-cache -f -t %{_datadir}/icons/hicolor &>/dev/null || :

%changelog
* Tue Oct 13 2026 mcda - 1.2.0-1
- Opção persistente de minimizar para o tray ao fechar, com menu Abrir e Sair
- Recuperação da janela quando o suporte ao tray desaparece
- Ícone com o símbolo de nove pontos do Tailscale
* Fri Oct 09 2026 mcda - 1.1.0-1
- Corrige leitura de preferências, operações assíncronas e navegação
- Adiciona autenticação via navegador e autorização via polkit
- Novo ícone vetorial e PNG
* Sun Apr 19 2026 mcda - 1.0.0-1
- Versão inicial com GTK4/libadwaita
- Status, lista de peers, exit node, toggles de configuração
