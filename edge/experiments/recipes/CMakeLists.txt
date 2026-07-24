add_executable(
    recipe_control_simulation
    recipe_control_simulation.cpp
)
target_link_libraries(
    recipe_control_simulation
    PRIVATE
        recipe_json
)
target_compile_features(
    recipe_control_simulation
    PRIVATE cxx_std_17
)
target_compile_definitions(
    recipe_control_simulation
    PRIVATE
        SMARTHYDRO_EXAMPLE_RECIPE_PATH="${PROJECT_SOURCE_DIR}/../config/example_recipe.json"
)
set_target_properties(
    recipe_control_simulation
    PROPERTIES CXX_EXTENSIONS OFF
)

find_program(GNUPLOT_EXECUTABLE gnuplot)
if(BUILD_TESTING AND GNUPLOT_EXECUTABLE)
    add_test(
        NAME experiment_recipe_control_simulation
        COMMAND
            ${CMAKE_COMMAND}
            -DEXPERIMENT_EXECUTABLE=$<TARGET_FILE:recipe_control_simulation>
            -DOUTPUT_DIRECTORY=${CMAKE_CURRENT_BINARY_DIR}/recipe_control_smoke
            -P ${PROJECT_SOURCE_DIR}/tests/recipe_control_simulation_smoke.cmake
    )
endif()
