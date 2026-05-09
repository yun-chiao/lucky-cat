from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from solcx import compile_source, get_installed_solc_versions, install_solc
from web3 import Web3


def _update_env_contract_address(env_path: Path, contract_address: str) -> None:
    if not env_path.exists():
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith("CONTRACT_ADDRESS="):
            lines[i] = f"CONTRACT_ADDRESS={contract_address}"
            updated = True
            break

    if not updated:
        lines.append(f"CONTRACT_ADDRESS={contract_address}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    env_path = base_dir / ".env"
    contract_path = base_dir / "contracts" / "LuckyCatState.sol"

    load_dotenv(dotenv_path=env_path, override=True)

    rpc_url = os.getenv("MANTLE_RPC_URL", "").strip()
    private_key = os.getenv("PRIVATE_KEY", "").strip()
    chain_id = int(os.getenv("CHAIN_ID", "5000"))

    if not rpc_url:
        raise SystemExit("Missing MANTLE_RPC_URL in backend/.env")
    if not private_key:
        raise SystemExit("Missing PRIVATE_KEY in backend/.env")

    source = contract_path.read_text(encoding="utf-8")

    solc_version = "0.8.24"
    installed_versions = {str(v) for v in get_installed_solc_versions()}

    compile_kwargs: dict[str, object] = {
        "output_values": ["abi", "bin"],
    }

    if solc_version in installed_versions:
        print(f"Using cached solc {solc_version}...")
        compile_kwargs["solc_version"] = solc_version
    else:
        print(f"Installing solc {solc_version} (first run may take a while)...")
        try:
            install_solc(solc_version)
            compile_kwargs["solc_version"] = solc_version
        except Exception as exc:
            local_solc = shutil.which("solc")
            if not local_solc:
                raise SystemExit(
                    "Cannot download solc from solc-bin.ethereum.org and no local solc found. "
                    "Install local compiler with 'brew install solidity' and retry. "
                    f"Original error: {exc}"
                )
            print(f"Falling back to local solc binary: {local_solc}")
            compile_kwargs["solc_binary"] = local_solc

    print("Compiling LuckyCatState.sol...")
    compiled = compile_source(
        source,
        **compile_kwargs,
    )

    contract_id = "<stdin>:LuckyCatState"
    if contract_id not in compiled:
        raise SystemExit("Failed to compile LuckyCatState contract")

    contract_interface = compiled[contract_id]
    abi = contract_interface["abi"]
    bytecode = contract_interface["bin"]

    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise SystemExit("Cannot connect to MANTLE_RPC_URL")

    account = web3.eth.account.from_key(private_key)
    nonce = web3.eth.get_transaction_count(account.address, "pending")
    gas_price = web3.eth.gas_price
    balance = web3.eth.get_balance(account.address)

    print(f"Deployer address: {account.address}")
    print(f"Deployer balance: {web3.from_wei(balance, 'ether')} MNT")

    contract = web3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor().build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gasPrice": gas_price,
            "gas": 1_500_000,
        }
    )

    try:
        tx["gas"] = web3.eth.estimate_gas(tx)
    except Exception:
        tx["gas"] = 1_500_000

    max_cost = tx["gas"] * tx["gasPrice"]
    if balance < max_cost:
        required = web3.from_wei(max_cost, "ether")
        current = web3.from_wei(balance, "ether")
        raise SystemExit(
            "Insufficient funds for deployment. "
            f"Need about {required} MNT, current balance {current} MNT."
        )

    signed = account.sign_transaction(tx)
    raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    tx_hash = web3.eth.send_raw_transaction(raw_tx)
    print(f"Deploy tx sent: {tx_hash.hex()}")

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=240)
    if receipt.status != 1:
        raise SystemExit("Deployment failed on-chain")

    deployed_address = receipt.contractAddress
    print(f"Contract deployed at: {deployed_address}")

    _update_env_contract_address(env_path, deployed_address)
    print("Updated backend/.env CONTRACT_ADDRESS")
    print("Restart backend to enable chain write path.")


if __name__ == "__main__":
    main()
