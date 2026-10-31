"""Research entry points; inclusion never grants collection or image-use permission."""

SOURCES = {
    "art-amerindien": {
        "directory_url": "https://www.art-amerindien.com/hallmarks/",
        "terms_url": "https://www.art-amerindien.com/",
    },
    "tskies": {
        "directory_url": "https://tskies.com/pages/silversmiths",
        "terms_url": "https://tskies.com/policies/terms-of-service",
    },
    "medicine-man": {
        "directory_url": "https://www.medicinemangallery.com/collections/"
        "identify-native-american-indian-jewelry-hallmarks",
        "terms_url": "https://www.medicinemangallery.com/policies/terms-of-service",
    },
    "horsekeeping": {
        "directory_url": "https://www.horsekeeping.com/native-american-jewelry-artifacts.htm",
        "terms_url": "https://www.horsekeeping.com/copyright_information.htm",
    },
    "kayben": {
        "directory_url": "https://www.kaybentreasures.com/hallmarks",
        "terms_url": "https://www.kaybentreasures.com/platform-and-privacy",
    },
    "perry-null": {
        "directory_url": "https://perrynulltrading.com/pages/artists",
        "terms_url": "https://perrynulltrading.com/policies/terms-of-service",
        "canonical_directory_url": "https://turquoisetraders.com/pages/artists",
        "canonical_terms_url": "https://turquoisetraders.com/policies/terms-of-service",
    },
}

# Different URLs and domains can repeat the same attribution. Unknown upstream
# provenance remains unknown even when two entries have different family labels.
SOURCE_FAMILIES = {
    "art-amerindien": "art-amerindien",
    "library-art-amerindien": "art-amerindien",
    "perry-null": "perry-null/turquoise-traders",
    "turquoise-traders": "perry-null/turquoise-traders",
}
