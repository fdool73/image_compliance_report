# GitHub Repository Dockerfile Analysis for Compliant Images

## Overview

This script analyzes GitHub repositories within your org using GraphQL to find all Docker images and validate usage of Docker images for compliance as defined in config.json. It generates three CSV reports summarizing the findings, one for compliant images, one for non compliant images and one for non compliant images found in build pipelines in your repo (e.g. .github/workflows/ci.yml).

## Features

- Reads configuration parameters from `config.json` file.
- Advanced error handling.
- **Repository Analysis**: Iterates through all repositories in the organization, checks for Docker images, and searches for specific image names.
- **CSV Report Generation**: Outputs the analysis results into three CSV file (`compliant_images.csv`, `non_compliant_images.csv`, `build_pipeline_images`).
- **Summary Statistics**: Prints summary statistics to the console.

## Requirements

- Python 3.x
- `Requests` for interacting with the GitHub API.

## Setup

1. **Config**:
    - Ensure GitHub token is set in config.json

## Usage

Run the script to generate the analysis report:

python app.py

