cmake_minimum_required(VERSION 3.10)
project(mod_audio_fork)

# C/C++ 标准
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# 依赖查找
find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBWEBSOCKETS REQUIRED libwebsockets)
pkg_check_modules(SPEEX REQUIRED speex)

# FreeSWITCH 路径（你的路径）
set(FREESWITCH_INCLUDE_DIR /Users/lindaw/freeswitch/include/freeswitch)
set(FREESWITCH_LIBRARY /Users/lindaw/freeswitch/lib/libfreeswitch.dylib)

# speexdsp
find_library(SPEEXDSP_LIBRARY NAMES speexdsp PATHS /opt/homebrew/lib)

# Boost（只使用头文件，不链接库）
set(Boost_FOUND TRUE)
set(BOOST_INCLUDE_DIR /opt/homebrew/include)

# 源文件
set(MOD_AUDIO_FORK_SOURCES
    mod_audio_fork.c
    lws_glue.cpp
    parser.cpp
    audio_pipe.cpp
    vector_math.cpp
)

# 模块
add_library(mod_audio_fork MODULE ${MOD_AUDIO_FORK_SOURCES})

# 头文件路径
target_include_directories(mod_audio_fork PRIVATE
    ${FREESWITCH_INCLUDE_DIR}
    ${LIBWEBSOCKETS_INCLUDE_DIRS}
    ${SPEEX_INCLUDE_DIRS}
    ${BOOST_INCLUDE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}
)

# 编译宏
target_compile_definitions(mod_audio_fork PRIVATE
    MOD_AUDIO_FORK_BUILD
    SWITCH_PACKAGE_VERSION="1.0.0"
)

# 编译选项
target_compile_options(mod_audio_fork PRIVATE
    -Wall -Wextra -fPIC -O2
)

# 链接库（**关键：去掉 boost、去掉 dl**）
target_link_libraries(mod_audio_fork PRIVATE
    ${FREESWITCH_LIBRARY}
    ${LIBWEBSOCKETS_LIBRARIES}
    ${SPEEX_LIBRARIES}
    ${SPEEXDSP_LIBRARY}
    pthread
)

# macOS 关键设置
set_target_properties(mod_audio_fork PROPERTIES
    PREFIX ""
    SUFFIX ".so"
    BUNDLE OFF
    SKIP_BUILD_RPATH FALSE
    BUILD_WITH_INSTALL_RPATH TRUE
)

# 屏蔽 Linux 链接参数
# target_link_options(mod_audio_fork PRIVATE
#     -Wl,-no-undefined
#     -Wl,-shared
# )

# 输出信息
message(STATUS "=== mod_audio_fork Configuration ===")
message(STATUS "FreeSWITCH include dir: ${FREESWITCH_INCLUDE_DIR}")
message(STATUS "FreeSWITCH library: ${FREESWITCH_LIBRARY}")
message(STATUS "libwebsockets: ${LIBWEBSOCKETS_LIBRARIES}")
message(STATUS "speex: ${SPEEX_LIBRARIES}")
message(STATUS "speexdsp: ${SPEEXDSP_LIBRARY}")
message(STATUS "boost include dir: ${BOOST_INCLUDE_DIR}")
