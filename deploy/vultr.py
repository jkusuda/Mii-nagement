"""Create the Vultr server for the web deployment (deploy/README.md).

    VULTR_API_KEY=... python3 deploy/vultr.py [--region atl] [--plan vc2-2c-4gb]

Registers your SSH public key, starts Ubuntu 24.04 with Docker installed (cloud-init),
waits for its IP address and prints the next steps. Running it again finds the server
by its label instead of making a second one.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = "https://api.vultr.com/v2"
LABEL = "mii-nagement"
# First boot: Docker from Ubuntu's own packages, and a firewall with only SSH and the site open.
CLOUD_INIT = """#cloud-config
package_update: true
packages: [docker.io, docker-compose-v2, rsync]
runcmd:
  - systemctl enable --now docker
  - ufw allow 22/tcp
  - ufw allow 80/tcp
  - ufw allow 443/tcp
  - ufw --force enable
"""


def call(method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['VULTR_API_KEY']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        text = response.read()
    return json.loads(text) if text else {}


def ssh_key_id() -> str:
    """The id of your SSH public key on Vultr, uploading it the first time."""
    public = next((p for p in (Path.home() / ".ssh").glob("id_*.pub")), None)
    if public is None:
        sys.exit("No SSH key in ~/.ssh: make one with `ssh-keygen -t ed25519` first.")
    key = public.read_text().strip()
    for existing in call("GET", "/ssh-keys?per_page=500")["ssh_keys"]:
        if existing["ssh_key"].split()[:2] == key.split()[:2]:
            return existing["id"]
    return call("POST", "/ssh-keys", {"name": f"{LABEL} ({public.name})", "ssh_key": key})["ssh_key"]["id"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default="atl", help="a Vultr region id (atl = Atlanta)")
    parser.add_argument("--plan", default="vc2-2c-4gb", help="a Vultr plan id")
    args = parser.parse_args()
    if not os.environ.get("VULTR_API_KEY"):
        sys.exit("Set VULTR_API_KEY (Vultr: Account > API).")

    found = [i for i in call("GET", "/instances?per_page=500")["instances"] if i["label"] == LABEL]
    if found:
        instance = found[0]
        print(f"Found the server {instance['id']}.")
    else:
        ubuntu = next(o for o in call("GET", "/os?per_page=500")["os"] if o["name"] == "Ubuntu 24.04 LTS x64")
        instance = call(
            "POST",
            "/instances",
            {
                "region": args.region,
                "plan": args.plan,
                "os_id": ubuntu["id"],
                "label": LABEL,
                "hostname": LABEL,
                "sshkey_id": [ssh_key_id()],
                "user_data": base64.b64encode(CLOUD_INIT.encode()).decode(),
            },
        )["instance"]
        print(f"Created the server {instance['id']} ({args.plan} in {args.region}). Waiting for its address...")

    while instance["main_ip"] in ("", "0.0.0.0") or instance["status"] != "active":
        time.sleep(5)
        instance = call("GET", f"/instances/{instance['id']}")["instance"]
    ip = instance["main_ip"]
    print(f"""
Server: {ip}
1. In deploy/server.env (copy server.env.example): MII_DOMAIN={ip}.sslip.io
   (or your own domain, with an A record pointing at {ip})
2. Give cloud-init a minute or two to install Docker, then: deploy/push.sh root@{ip}
""")


if __name__ == "__main__":
    main()
