from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from pipe1_license_server.admin import AdminService
from pipe1_license_server.settings import ServerSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipe1-admin")
    subcommands = parser.add_subparsers(dest="resource", required=True)

    org = subcommands.add_parser("org")
    org_sub = org.add_subparsers(dest="action", required=True)
    org_create = org_sub.add_parser("create")
    org_create.add_argument("--name", required=True)
    org_create.add_argument("--contact-email")
    org_sub.add_parser("list")

    license_cmd = subcommands.add_parser("license")
    license_sub = license_cmd.add_subparsers(dest="action", required=True)
    license_create = license_sub.add_parser("create")
    license_create.add_argument("--org", required=True)
    license_create.add_argument("--plan", required=True)
    license_create.add_argument("--device-limit", type=int, required=True)
    license_create.add_argument("--expires-at", required=True)
    license_list = license_sub.add_parser("list")
    license_list.add_argument("--org")

    key = subcommands.add_parser("key")
    key_sub = key.add_subparsers(dest="action", required=True)
    key_generate = key_sub.add_parser("generate")
    key_generate.add_argument("--license", required=True)
    key_generate.add_argument("--type", default="production")
    key_list = key_sub.add_parser("list")
    key_list.add_argument("--license")
    key_list.add_argument("--status", choices=["active", "revoked", "inactive"])
    key_revoke = key_sub.add_parser("revoke")
    key_revoke.add_argument("--key-prefix", required=True)
    key_rotate = key_sub.add_parser("rotate")
    key_rotate.add_argument("--key-prefix", required=True)

    devices = subcommands.add_parser("devices")
    devices_sub = devices.add_subparsers(dest="action", required=True)
    devices_list = devices_sub.add_parser("list")
    devices_list.add_argument("--license")
    devices_list.add_argument(
        "--all",
        action="store_true",
        help="include deactivated and revoked activations",
    )

    device = subcommands.add_parser("device")
    device_sub = device.add_subparsers(dest="action", required=True)
    device_deactivate = device_sub.add_parser("deactivate")
    device_deactivate.add_argument("--activation", required=True)

    feature = subcommands.add_parser("feature")
    feature_sub = feature.add_subparsers(dest="action", required=True)
    feature_set = feature_sub.add_parser("set")
    feature_set.add_argument("--license", required=True)
    feature_set.add_argument("--feature", required=True)
    feature_set.add_argument("--enabled", choices=["true", "false"], required=True)
    feature_list = feature_sub.add_parser("list")
    feature_list.add_argument("--license")

    quota = subcommands.add_parser("quota")
    quota_sub = quota.add_subparsers(dest="action", required=True)
    quota_set = quota_sub.add_parser("set")
    quota_set.add_argument("--license", required=True)
    quota_set.add_argument("--feature", required=True)
    quota_set.add_argument("--unit", required=True)
    quota_set.add_argument("--limit", type=int, required=True)
    quota_set.add_argument("--period", required=True)
    quota_list = quota_sub.add_parser("list")
    quota_list.add_argument("--license")

    usage = subcommands.add_parser("usage")
    usage_sub = usage.add_subparsers(dest="action", required=True)
    usage_list = usage_sub.add_parser("list")
    usage_list.add_argument("--license", required=True)

    audit = subcommands.add_parser("audit")
    audit_sub = audit.add_subparsers(dest="action", required=True)
    audit_sub.add_parser("list")

    release = subcommands.add_parser("release")
    release_sub = release.add_subparsers(dest="action", required=True)
    release_create = release_sub.add_parser("create")
    release_create.add_argument("--version", required=True)
    release_create.add_argument("--platform", default="windows")
    release_create.add_argument("--arch", default="x64")
    release_create.add_argument("--channel", default="stable")
    release_create.add_argument("--download-url", required=True)
    release_create.add_argument("--file")
    release_create.add_argument("--sha256")
    release_create.add_argument("--size-bytes", type=int)
    release_create.add_argument("--notes")
    release_create.add_argument("--mandatory", action="store_true")
    release_create.add_argument("--min-supported-version")
    release_create.add_argument(
        "--status", choices=["draft", "published", "disabled"], default="draft"
    )
    release_list = release_sub.add_parser("list")
    release_list.add_argument("--platform")
    release_list.add_argument("--channel")
    release_list.add_argument("--status")
    release_publish = release_sub.add_parser("publish")
    _add_release_target_args(release_publish)
    release_disable = release_sub.add_parser("disable")
    _add_release_target_args(release_disable)
    return parser


def run_cli(
    argv: list[str],
    *,
    settings: ServerSettings | None = None,
    stdout: TextIO | None = None,
) -> dict[str, Any]:
    args = _parser().parse_args(argv)
    service = AdminService(settings or ServerSettings())
    output: dict[str, Any]

    if args.resource == "org" and args.action == "create":
        output = {
            "id": service.create_organization(args.name, args.contact_email),
            "name": args.name,
        }
    elif args.resource == "org" and args.action == "list":
        output = {"organizations": service.list_organizations()}
    elif args.resource == "license" and args.action == "create":
        output = {
            "id": service.create_license(
                organization_id=args.org,
                plan=args.plan,
                device_limit=args.device_limit,
                expires_at=args.expires_at,
            )
        }
    elif args.resource == "license" and args.action == "list":
        output = {"licenses": service.list_licenses(args.org)}
    elif args.resource == "key" and args.action == "generate":
        output = {
            "license_key": service.generate_license_key(
                args.license, key_type=args.type
            )
        }
    elif args.resource == "key" and args.action == "list":
        output = {"keys": service.list_license_keys(args.license, args.status)}
    elif args.resource == "key" and args.action == "revoke":
        service.revoke_license_key(args.key_prefix)
        output = {"key_prefix": args.key_prefix, "status": "revoked"}
    elif args.resource == "key" and args.action == "rotate":
        output = {"license_key": service.rotate_license_key(args.key_prefix)}
    elif args.resource == "devices" and args.action == "list":
        output = {
            "devices": service.list_device_activations(
                args.license, active_only=not args.all
            )
        }
    elif args.resource == "device" and args.action == "deactivate":
        service.deactivate_device(args.activation)
        output = {"activation_id": args.activation, "status": "deactivated"}
    elif args.resource == "feature" and args.action == "set":
        enabled = args.enabled == "true"
        service.set_feature(args.license, args.feature, enabled)
        output = {
            "license_id": args.license,
            "feature": args.feature,
            "enabled": enabled,
        }
    elif args.resource == "feature" and args.action == "list":
        output = {"features": service.list_features(args.license)}
    elif args.resource == "quota" and args.action == "set":
        service.set_ai_quota(
            args.license,
            feature_key=args.feature,
            unit=args.unit,
            limit=args.limit,
            period=args.period,
        )
        output = {
            "license_id": args.license,
            "feature": args.feature,
            "limit": args.limit,
        }
    elif args.resource == "quota" and args.action == "list":
        output = {"quotas": service.list_quotas(args.license)}
    elif args.resource == "usage" and args.action == "list":
        output = {"usage_events": service.list_usage_events(args.license)}
    elif args.resource == "audit" and args.action == "list":
        output = {"audit_events": service.list_audit_events()}
    elif args.resource == "release" and args.action == "create":
        sha256, size_bytes = _release_file_metadata(
            args.file,
            args.sha256,
            args.size_bytes,
        )
        output = {
            "release": service.create_release(
                version=args.version,
                platform=args.platform,
                arch=args.arch,
                channel=args.channel,
                download_url=args.download_url,
                sha256=sha256,
                size_bytes=size_bytes,
                release_notes=args.notes,
                mandatory=args.mandatory,
                min_supported_version=args.min_supported_version,
                status=args.status,
            )
        }
    elif args.resource == "release" and args.action == "list":
        output = {
            "releases": service.list_releases(
                platform=args.platform,
                channel=args.channel,
                status=args.status,
            )
        }
    elif args.resource == "release" and args.action == "publish":
        output = {
            "release": service.publish_release(
                version=args.version,
                platform=args.platform,
                arch=args.arch,
                channel=args.channel,
            )
        }
    elif args.resource == "release" and args.action == "disable":
        output = {
            "release": service.disable_release(
                version=args.version,
                platform=args.platform,
                arch=args.arch,
                channel=args.channel,
            )
        }
    else:  # pragma: no cover - argparse prevents this path
        raise SystemExit(2)

    target = stdout or sys.stdout
    target.write(json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n")
    return output


def _add_release_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", default="windows")
    parser.add_argument("--arch", default="x64")
    parser.add_argument("--channel", default="stable")


def _release_file_metadata(
    file_path: str | None,
    sha256: str | None,
    size_bytes: int | None,
) -> tuple[str, int]:
    if file_path:
        payload = Path(file_path).read_bytes()
        return hashlib.sha256(payload).hexdigest(), len(payload)
    if sha256 and size_bytes is not None:
        return sha256, size_bytes
    raise ValueError("Either --file or both --sha256 and --size-bytes are required.")


def main() -> None:
    run_cli(sys.argv[1:])
