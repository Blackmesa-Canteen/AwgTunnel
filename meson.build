project(
  'awg-tunnel',
  version: '0.1.0',
  license: 'GPL-3.0-or-later',
  meson_version: '>= 1.0.0',
)

app_id = 'io.github.blackmesa_canteen.AwgTunnel'
python = import('python').find_installation('python3')

prefix = get_option('prefix')
datadir = prefix / get_option('datadir')
bindir = prefix / get_option('bindir')
pkgdatadir = datadir / meson.project_name()
moduledir = pkgdatadir / 'awgtunnel'

# The launcher puts pkgdatadir on sys.path explicitly rather than relying on
# the runtime's site-packages layout, which differs between runtime versions.
build_conf = configuration_data()
build_conf.set('APP_ID', app_id)
build_conf.set('VERSION', meson.project_version())
build_conf.set('PKGDATADIR', pkgdatadir)

configure_file(
  input: 'src/awgtunnel/build_config.py.in',
  output: 'build_config.py',
  configuration: build_conf,
  install: true,
  install_dir: moduledir,
)

install_data(
  files(
    'src/awgtunnel/__init__.py',
    'src/awgtunnel/application.py',
    'src/awgtunnel/engine.py',
    'src/awgtunnel/main.py',
    'src/awgtunnel/profile.py',
    'src/awgtunnel/socks.py',
    'src/awgtunnel/storage.py',
    'src/awgtunnel/window.py',
  ),
  install_dir: moduledir,
)

launcher_conf = configuration_data()
launcher_conf.set('PYTHON', python.full_path())
launcher_conf.set('PKGDATADIR', pkgdatadir)
configure_file(
  input: 'src/awg-tunnel.in',
  output: 'awg-tunnel',
  configuration: launcher_conf,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

install_data(
  f'data/@app_id@.desktop',
  install_dir: datadir / 'applications',
)

install_data(
  f'data/@app_id@.metainfo.xml',
  install_dir: datadir / 'metainfo',
)

install_data(
  f'data/icons/@app_id@.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)

install_data(
  f'data/icons/@app_id@-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'apps',
)

# Validation is wired into `meson test` so a broken desktop file or metainfo
# fails the build rather than surfacing at install time.
desktop_file_validate = find_program('desktop-file-validate', required: false)
if desktop_file_validate.found()
  test(
    'desktop-file',
    desktop_file_validate,
    args: [meson.current_source_dir() / f'data/@app_id@.desktop'],
  )
endif

appstreamcli = find_program('appstreamcli', required: false)
if appstreamcli.found()
  test(
    'metainfo',
    appstreamcli,
    args: [
      'validate',
      '--no-net',
      meson.current_source_dir() / f'data/@app_id@.metainfo.xml',
    ],
  )
endif

pytest = find_program('pytest', required: false)
if pytest.found()
  test(
    'unit',
    pytest,
    args: ['-q', meson.current_source_dir() / 'tests'],
    env: {'PYTHONPATH': meson.current_source_dir() / 'src'},
    timeout: 120,
  )
endif
