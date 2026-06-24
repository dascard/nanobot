import sys
import os
import argparse

# Ensure we can import from the parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients.model_registry import registry

def list_models(args):
    models = registry.data.get("models", [])
    if not models:
        print("No models found in registry.")
        return

    print(f"\n{'ID':<30} | {'Provider':<12} | {'Tier':<8} | {'Intel':<5} | {'Cost(In)':<8}")
    print("-" * 75)
    for m in models:
        print(f"{m.get('id'):<30} | {m.get('provider'):<12} | {m.get('tier'):<8} | {m.get('intelligence'):<5} | {m.get('cost_input_1m'):<8}")
    print(f"\nLast Updated: {registry.data.get('last_updated')}\n")

def add_or_update(args):
    model_data = {
        "id": args.id,
        "provider": args.provider,
        "tier": args.tier,
        "intelligence": args.intelligence,
        "cost_input_1m": args.cost_in,
        "cost_output_1m": args.cost_out,
        "tags": args.tags.split(",") if args.tags else []
    }
    registry.add_or_update_model(model_data)
    print(f"Successfully added/updated model: {args.id}")

def remove_model(args):
    registry.remove_model(args.id)
    print(f"Successfully removed model: {args.id}")

def main():
    parser = argparse.ArgumentParser(description="Nanobot Model Registry Manager")
    subparsers = parser.add_subparsers(dest="command")

    # List command
    subparsers.add_parser("list", help="List all models")

    # Add/Update command
    add_parser = subparsers.add_parser("add", help="Add or update a model")
    add_parser.add_argument("--id", required=True, help="Official Model ID")
    add_parser.add_argument("--provider", required=True, help="Provider name (gemini, deepseek, etc.)")
    add_parser.add_argument("--tier", default="smart", choices=["smart", "fast", "reasoning"], help="Model tier")
    add_parser.add_argument("--intelligence", type=int, default=5, help="Intelligence score (1-10)")
    add_parser.add_argument("--cost-in", type=float, default=0.0, help="Cost per 1M input tokens")
    add_parser.add_argument("--cost-out", type=float, default=0.0, help="Cost per 1M output tokens")
    add_parser.add_argument("--tags", help="Comma-separated list of tags")

    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a model")
    remove_parser.add_argument("--id", required=True, help="Model ID to remove")

    args = parser.parse_args()

    if args.command == "list":
        list_models(args)
    elif args.command == "add":
        add_or_update(args)
    elif args.command == "remove":
        remove_model(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
