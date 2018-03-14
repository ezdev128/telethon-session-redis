import setuptools

setuptools.setup(
    name="telethon-session-redis",
    version="0.1.0",
    url="https://github.com/ezdev128/telethon-session-redis",

    author="Konstantin M.",
    author_email="ezdev128@yandex.com",

    description="Redis backend for Telethon session storage",
    long_description=open("README.rst").read(),

    packages=setuptools.find_packages(),

    install_requires=[
        "redis>=2.0",
    ],

    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.4",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
    ],
    python_requires="~=3.4",
)
