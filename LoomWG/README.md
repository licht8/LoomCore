<p align="center">
  <img src="assets/loomwg-logo.png" alt="LoomWG logo" width="200" />
</p>

<h1 align="center">LoomWG</h1>

<p align="center">
  LoomWG is a professional Python-based CLI application for installing, configuring, managing, monitoring, and troubleshooting WireGuard VPN servers on CentOS, Rocky Linux, AlmaLinux, Ubuntu, Debian, and other RHEL-family Linux distributions.
</p>

## Features

- **WireGuard Installation**: Automated, idempotent installation with system verification for CentOS/RHEL-family servers
- **Server Configuration**: Interactive setup with validation and sensible defaults
- **Peer Management**: Create, list, disable, enable, revoke, rotate keys, and remove VPN peers
- **IP Allocation**: Automatic private IP assignment with conflict prevention
- **Firewall Integration**: Automatic firewalld configuration with port management
- **Network Configuration**: IPv4/IPv6 forwarding setup and verification
- **Diagnostics**: Comprehensive health checks across all subsystems
- **Backup & Restore**: Full configuration snapshots with recovery capabilities
- **Logging**: Detailed operational logs without exposing secrets
- **CLI Interface**: Interactive menu-driven terminal interface

## Requirements

- **OS**: CentOS Stream 8/9/10, Rocky Linux 8/9/10, AlmaLinux 8/9/10, Ubuntu LTS, Debian, or other RHEL-family Linux distributions
- **Root privileges**: Required for all operations
- **Python**: 3.12+
- **systemd**: For service management
- **firewalld**: For firewall management
- **WireGuard kernel module**: Provided by the OS package set or ELRepo on supported distributions

## Installation

```bash
git clone https://github.com/licht8/LoomWG.git
cd LoomWG
chmod +x ./run.sh
```

The project script will bootstrap the environment as needed and can be used as the recommended startup flow:

```bash
./run.sh
```

## Configuration

### Server Configuration

WireGuard configuration is stored at `/etc/wireguard/wg0.conf` with secure permissions (600).

Default settings:
- Interface: `wg0`
- IPv4 Network: `10.66.66.0/24`
- IPv6 Network: `fd42:42:42::/64`
- Listen Port: `51820`
- DNS: `1.1.1.1`, `1.0.0.1`

### Peer Database

Peer information is stored in the app directory at `<project-root>/data/peers.json` for tracking and management.

### Client Configs

Exported user/client `.conf` files are stored in `<project-root>/data/clients/`.
Generated QR code images are stored in `<project-root>/data/qr_codes/` and are also printed in the console after peer creation.

### Logs

Operational logs are stored at `<project-root>/logs/loomwg_YYYYMMDD.log`.

### Backups

Configuration backups are stored at `<project-root>/backups/`.

### Testing

Run tests with pytest:

```bash
pytest tests/
```

## Troubleshooting

### WireGuard Installation Fails

Run diagnostics to identify issues:

```bash
# From main menu → Diagnostics → Full health check
```

### Port Already in Use

Check what's using port 51820:

```bash
sudo netstat -tlnp | grep 51820
```

Change the listen port in server configuration if needed.

### Firewall Issues

Verify firewall configuration:

```bash
# From main menu → Firewall → Show status
# Or from Diagnostics → Firewall diagnostics
```

### Peer Cannot Connect

1. Check server is running: `ip link show wg0`
2. Verify peer config has correct server endpoint
3. Check firewall allows UDP 51820
4. Review logs: Main menu → Logs → View recent logs

## License

MIT License - See LICENSE file for details

## Support

For issues, questions, or suggestions, please open an issue on the repository.

---
