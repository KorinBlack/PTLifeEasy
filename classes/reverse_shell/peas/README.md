# PEAS Binaries Directory

Place your PEASS-ng binaries here for the `privesc_enum` shortcut.

## Required Files

- **winpeas.exe** - Windows privilege escalation enumeration script
- **linpeas.sh** - Linux privilege escalation enumeration script

## Download

Download the latest versions from the official PEASS-ng repository:
https://github.com/peass-ng/PEASS-ng/releases

### Recommended files:
- `winpeas.exe` (or `winPEASx64.exe` / `winPEASany.exe`)
- `linpeas.sh`

## Usage

Once placed here, the `privesc_enum` shortcut in the Reverse Shell Handler will:
1. Auto-detect the target OS (Windows/Linux)
2. Upload the appropriate PEAS binary via base64
3. Execute it on the target
4. Stream the output back to your terminal

## Note

These binaries are NOT included in this repository. You must download them yourself.
They are third-party tools from the PEASS-ng project by carlospolop.
