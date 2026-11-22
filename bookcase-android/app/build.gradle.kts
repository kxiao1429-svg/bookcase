import java.util.Properties

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// 发布签名信息从本地 keystore.properties 读取（该文件不入库，见 .gitignore）。
// 没有它时 release 包不签名但仍可编译；正式发布请按 README「签名」一节配置。
val keystorePropsFile = rootProject.file("keystore.properties")
val keystoreProps = Properties().apply {
    if (keystorePropsFile.isFile) keystorePropsFile.inputStream().use { load(it) }
}
val releaseStoreFile = keystoreProps.getProperty("storeFile")?.let { rootProject.file(it) }
val hasReleaseSigning = releaseStoreFile?.isFile == true

android {
    namespace = "com.bookcase.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.bookcase.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 13
        versionName = "1.12.0"

        ndk {
            // 只打真机常用的 64 位 ARM；需要模拟器再加 "x86_64"
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        // 自用签名：jks 和口令都只放在本地 keystore.properties 里，不进仓库。
        if (hasReleaseSigning) {
            create("release") {
                storeFile = releaseStoreFile
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        // 书库只用标准库（sqlite3 / zipfile / http.server），不需要额外 pip 包。
        // PyMuPDF 在 Android 上没有轮子，scan.py 已经做了容错：PDF 只是没有封面和页数。
    }
}
