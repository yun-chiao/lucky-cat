// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract LuckyCatState {
    string public currentState;
    string public lastAIMessage;
    uint256 public lastUpdated;
    address public owner;

    event StateUpdated(string indexed state, string message, uint256 indexed updatedAt);

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    constructor() {
        owner = msg.sender;
        currentState = "idle";
        lastAIMessage = "";
        lastUpdated = block.timestamp;
    }

    function updateState(string memory _state, string memory _message) external onlyOwner {
        currentState = _state;
        lastAIMessage = _message;
        lastUpdated = block.timestamp;

        emit StateUpdated(_state, _message, lastUpdated);
    }
}
