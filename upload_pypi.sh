#!/bin/bash
set -e

TESTPYPI_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --testpypi-only)
            TESTPYPI_ONLY=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--testpypi-only]"
            exit 1
            ;;
    esac
done

rm -rf dist/ build/ *.egg-info

echo "Building package..."
python -m build

VERSION=$(python setup.py --version 2>/dev/null || git describe --abbrev=0 --tags | sed 's/^v//')

echo "Version: $VERSION"

if $TESTPYPI_ONLY; then
    read -p "Publish version $VERSION to TestPyPI only? (y/n) " -n 1 -r
else
    read -p "Publish version $VERSION to PyPI? (y/n) " -n 1 -r
fi
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi

echo "Uploading to TestPyPI..."
twine upload --repository testpypi dist/*

if $TESTPYPI_ONLY; then
    echo "Done (TestPyPI only)!"
    exit 0
fi

read -p "Test upload successful? Publish to PyPI? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
fi

echo "Uploading to PyPI..."
twine upload dist/*

echo "Done!"