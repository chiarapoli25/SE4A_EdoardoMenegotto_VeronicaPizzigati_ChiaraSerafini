#include "smarthydro/control_system.hpp"
#include "smarthydro/recipe_json.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

constexpr double kStepHours = 0.25;
constexpr double kStepSeconds = kStepHours * 3600.0;
constexpr double kPi = 3.14159265358979323846;

using smarthydro::ControlledVariable;
using smarthydro::ControlledValues;

struct ProcessState {
    double soil_moisture_percent = 48.0;
    double light_ppfd = 0.0;
    double ph = 5.80;
    double nitrogen_milligrams_per_liter = 110.0;
    double phosphorus_milligrams_per_liter = 35.0;
    double potassium_milligrams_per_liter = 150.0;
};

struct Commands {
    double water_liters = 0.0;
    double light_percent = 0.0;
    double ph_milliliters = 0.0;
    double nitrogen_milliliters = 0.0;
    double phosphorus_milliliters = 0.0;
    double potassium_milliliters = 0.0;
};

std::size_t index_of(ControlledVariable variable) {
    return smarthydro::controlled_variable_index(variable);
}

double hour_of_day(double elapsed_hours) {
    return std::fmod(elapsed_hours, 24.0);
}

double natural_light_ppfd(double hour) {
    if (hour < 6.0 || hour >= 18.0) {
        return 0.0;
    }
    return 280.0 * std::sin(kPi * (hour - 6.0) / 12.0);
}

bool gnuplot_available() {
#ifdef _WIN32
    return std::system("gnuplot --version >NUL 2>&1") == 0;
#else
    return std::system("gnuplot --version >/dev/null 2>&1") == 0;
#endif
}

#ifdef _WIN32
FILE* open_gnuplot() {
    return _popen("gnuplot", "w");
}

int close_gnuplot(FILE* pipe) {
    return _pclose(pipe);
}
#else
FILE* open_gnuplot() {
    return popen("gnuplot", "w");
}

int close_gnuplot(FILE* pipe) {
    return pclose(pipe);
}
#endif

std::string gnuplot_quote(const std::filesystem::path& path) {
    std::string escaped = path.string();
    std::size_t position = 0;
    while ((position = escaped.find('\'', position)) != std::string::npos) {
        escaped.insert(position, 1, '\'');
        position += 2;
    }
    return "'" + escaped + "'";
}

void confirm_all(smarthydro::RecipeControlSystem& system) {
    for (std::size_t index = 0;
         index < smarthydro::kControlledVariableCount;
         ++index) {
        const auto result = system.confirm_configuration(
            static_cast<ControlledVariable>(index));
        if (!result.success) {
            throw std::runtime_error(
                "conferma fallita per " +
                std::string(smarthydro::to_string(
                    static_cast<ControlledVariable>(index))) +
                ": " + result.error);
        }
    }
    if (!system.all_configurations_confirmed()) {
        throw std::runtime_error(
            "la ricetta non risulta completamente confermata");
    }
}

smarthydro::ControlRequest sensor_request(
    double value,
    double elapsed_hours,
    double current_hour) {
    smarthydro::ControlRequest request;
    request.controller_input.measured_value = value;
    request.controller_input.delta_time_seconds = kStepSeconds;
    request.elapsed_recipe_hours = elapsed_hours;
    request.simulated_time_seconds = elapsed_hours * 3600.0;
    request.hour_of_day = current_hour;
    return request;
}

smarthydro::ControlRequest nutrient_request(
    double estimate,
    double elapsed_hours,
    double current_hour,
    double water_liters,
    double cumulative_dose,
    double daily_dose,
    double seconds_since_last_dose) {
    smarthydro::ControlRequest request;
    request.controller_input.model_estimate = estimate;
    request.controller_input.delta_time_seconds = kStepSeconds;
    request.controller_input.water_delivered_liters = water_liters;
    request.controller_input.cumulative_dose_milliliters = cumulative_dose;
    request.elapsed_recipe_hours = elapsed_hours;
    request.simulated_time_seconds = elapsed_hours * 3600.0;
    request.hour_of_day = current_hour;
    request.daily_dose_milliliters = daily_dose;
    request.seconds_since_last_dose = seconds_since_last_dose;
    return request;
}

double command_or_throw(
    const smarthydro::ControlDecision& decision,
    ControlledVariable variable) {
    if (decision.status == smarthydro::ControlDecisionStatus::BLOCKED) {
        if (decision.command == 0.0 &&
            (decision.message == "pH settling time is still active" ||
             decision.message ==
                 "minimum interval between doses is active" ||
             decision.message == "daily dose limit reached")) {
            return 0.0;
        }
        throw std::runtime_error(
            "controllo bloccato per " +
            std::string(smarthydro::to_string(variable)) +
            ": " + decision.message);
    }
    return decision.command;
}

void update_process(
    ProcessState& state,
    const Commands& commands,
    double current_hour) {
    const double natural_light = natural_light_ppfd(current_hour);
    state.light_ppfd =
        natural_light + commands.light_percent * 3.5;

    state.soil_moisture_percent = std::clamp(
        state.soil_moisture_percent -
            0.24 * kStepHours +
            30.0 * commands.water_liters,
        0.0,
        100.0);

    state.ph = std::clamp(
        state.ph + 0.02 * commands.ph_milliliters,
        0.0,
        14.0);

    const double dilution = 1.0 + 0.08 * commands.water_liters;
    state.nitrogen_milligrams_per_liter = std::max(
        0.0,
        state.nitrogen_milligrams_per_liter / dilution -
            0.18 * kStepHours +
            1.35 * commands.nitrogen_milliliters);
    state.phosphorus_milligrams_per_liter = std::max(
        0.0,
        state.phosphorus_milligrams_per_liter / dilution -
            0.04 * kStepHours +
            0.65 * commands.phosphorus_milliliters);
    state.potassium_milligrams_per_liter = std::max(
        0.0,
        state.potassium_milligrams_per_liter / dilution -
            0.20 * kStepHours +
            1.20 * commands.potassium_milliliters);
}

void write_header(std::ofstream& csv) {
    csv
        << "elapsed_hours,phase,soil_moisture_percent,light_ppfd,ph,"
        << "nitrogen_mg_per_liter,phosphorus_mg_per_liter,"
        << "potassium_mg_per_liter,water_command_liters,"
        << "light_command_percent,ph_command_milliliters,"
        << "nitrogen_command_milliliters,"
        << "phosphorus_command_milliliters,"
        << "potassium_command_milliliters,"
        << "cumulative_water_liters,cumulative_nitrogen_milliliters,"
        << "cumulative_phosphorus_milliliters,"
        << "cumulative_potassium_milliliters\n";
}

void write_sample(
    std::ofstream& csv,
    double elapsed_hours,
    const std::string& phase_name,
    const ProcessState& state,
    const Commands& commands,
    double cumulative_water,
    const ControlledValues<double>& cumulative_dose) {
    csv << std::fixed << std::setprecision(4)
        << elapsed_hours << ',' << phase_name << ','
        << state.soil_moisture_percent << ','
        << state.light_ppfd << ','
        << state.ph << ','
        << state.nitrogen_milligrams_per_liter << ','
        << state.phosphorus_milligrams_per_liter << ','
        << state.potassium_milligrams_per_liter << ','
        << commands.water_liters << ','
        << commands.light_percent << ','
        << commands.ph_milliliters << ','
        << commands.nitrogen_milliliters << ','
        << commands.phosphorus_milliliters << ','
        << commands.potassium_milliliters << ','
        << cumulative_water << ','
        << cumulative_dose[index_of(ControlledVariable::NITROGEN)] << ','
        << cumulative_dose[index_of(ControlledVariable::PHOSPHORUS)] << ','
        << cumulative_dose[index_of(ControlledVariable::POTASSIUM)] << '\n';
}

class LiveRecipePlot {
public:
    LiveRecipePlot() {
        pipe_ = open_gnuplot();
        if (pipe_ == nullptr) {
            throw std::runtime_error("impossibile avviare gnuplot");
        }
    }

    LiveRecipePlot(const LiveRecipePlot&) = delete;
    LiveRecipePlot& operator=(const LiveRecipePlot&) = delete;

    ~LiveRecipePlot() {
        if (pipe_ != nullptr) {
            std::fputs("unset multiplot\nexit\n", pipe_);
            std::fflush(pipe_);
            close_gnuplot(pipe_);
        }
    }

    void show(const std::filesystem::path& csv_path) {
        const std::string csv =
            gnuplot_quote(std::filesystem::absolute(csv_path));
        std::fprintf(
            pipe_,
            "set datafile separator ','\n"
            "set key top right\n"
            "set grid\n"
            "set multiplot layout 4,2 rowsfirst "
            "title 'SmartHydro - fase confermata: stato e comandi'\n"
            "set ylabel 'Umidita (%%)'\n"
            "plot %s using 1:3 with lines title 'Terreno'\n"
            "set ylabel 'Acqua (L)'\n"
            "plot %s using 1:9 with impulses title 'Pompa'\n"
            "set ylabel 'PPFD'\n"
            "plot %s using 1:4 with lines title 'Luce'\n"
            "set ylabel 'Comando (%%)'\n"
            "plot %s using 1:10 with steps title 'Lampade'\n"
            "set ylabel 'pH'\n"
            "plot %s using 1:5 with lines title 'pH'\n"
            "set ylabel 'Dose pH (mL)'\n"
            "plot %s using 1:11 with impulses title 'pH +/-'\n"
            "set xlabel 'Tempo fase (h)'\n"
            "set ylabel 'Concentrazione (mg/L)'\n"
            "plot %s using 1:6 with lines title 'N', "
            "'' using 1:7 with lines title 'P', "
            "'' using 1:8 with lines title 'K'\n"
            "set ylabel 'Dose (mL)'\n"
            "plot %s using 1:12 with impulses title 'N', "
            "'' using 1:13 with impulses title 'P', "
            "'' using 1:14 with impulses title 'K'\n"
            "unset multiplot\n",
            csv.c_str(),
            csv.c_str(),
            csv.c_str(),
            csv.c_str(),
            csv.c_str(),
            csv.c_str(),
            csv.c_str(),
            csv.c_str());
        if (std::fflush(pipe_) != 0) {
            throw std::runtime_error("gnuplot non risponde");
        }
    }

private:
    FILE* pipe_ = nullptr;
};

void run_experiment(const std::filesystem::path& output_directory) {
    const auto recipe =
        smarthydro::load_recipe_json(SMARTHYDRO_EXAMPLE_RECIPE_PATH);
    smarthydro::RecipeControlSystem control_system(recipe);
    confirm_all(control_system);

    std::filesystem::create_directories(output_directory);
    const auto csv_path =
        output_directory / "recipe_phase_simulation.csv";
    std::ofstream csv(csv_path);
    if (!csv) {
        throw std::runtime_error(
            "impossibile creare il CSV: " + csv_path.string());
    }
    write_header(csv);

    const double duration_hours =
        control_system.recipe().phases.front().duration_hours;
    const std::size_t step_count = static_cast<std::size_t>(
        std::ceil(duration_hours / kStepHours));
    ProcessState state;
    ControlledValues<double> cumulative_dose{};
    ControlledValues<double> daily_dose{};
    ControlledValues<double> seconds_since_last_dose{};
    seconds_since_last_dose.fill(1.0e30);
    double cumulative_water = 0.0;

    for (std::size_t step = 0; step <= step_count; ++step) {
        const double elapsed_hours = std::min(
            duration_hours, step * kStepHours);
        const double current_hour = hour_of_day(elapsed_hours);
        if (step > 0 && current_hour < kStepHours) {
            daily_dose.fill(0.0);
        }

        Commands commands;
        if (step == step_count) {
            write_sample(
                csv,
                elapsed_hours,
                control_system.recipe().phases.front().name,
                state,
                commands,
                cumulative_water,
                cumulative_dose);
            break;
        }

        auto request = sensor_request(
            state.soil_moisture_percent, elapsed_hours, current_hour);
        commands.water_liters = command_or_throw(
            control_system.execute(
                ControlledVariable::SOIL_MOISTURE, request),
            ControlledVariable::SOIL_MOISTURE);

        request = sensor_request(
            state.light_ppfd, elapsed_hours, current_hour);
        commands.light_percent = command_or_throw(
            control_system.execute(ControlledVariable::LIGHT, request),
            ControlledVariable::LIGHT);

        request = sensor_request(state.ph, elapsed_hours, current_hour);
        request.daily_dose_milliliters =
            daily_dose[index_of(ControlledVariable::PH)];
        request.seconds_since_last_dose =
            seconds_since_last_dose[index_of(ControlledVariable::PH)];
        commands.ph_milliliters = command_or_throw(
            control_system.execute(ControlledVariable::PH, request),
            ControlledVariable::PH);

        const std::array<std::pair<ControlledVariable, double*>, 3>
            nutrient_states{{
                {ControlledVariable::NITROGEN,
                 &state.nitrogen_milligrams_per_liter},
                {ControlledVariable::PHOSPHORUS,
                 &state.phosphorus_milligrams_per_liter},
                {ControlledVariable::POTASSIUM,
                 &state.potassium_milligrams_per_liter},
            }};
        std::array<double*, 3> nutrient_commands{{
            &commands.nitrogen_milliliters,
            &commands.phosphorus_milliliters,
            &commands.potassium_milliliters,
        }};
        for (std::size_t nutrient = 0;
             nutrient < nutrient_states.size();
             ++nutrient) {
            const auto variable = nutrient_states[nutrient].first;
            const auto index = index_of(variable);
            auto nutrient_control_request = nutrient_request(
                *nutrient_states[nutrient].second,
                elapsed_hours,
                current_hour,
                commands.water_liters,
                cumulative_dose[index],
                daily_dose[index],
                seconds_since_last_dose[index]);
            *nutrient_commands[nutrient] = command_or_throw(
                control_system.execute(
                    variable, nutrient_control_request),
                variable);
        }

        write_sample(
            csv,
            elapsed_hours,
            control_system.recipe().phases.front().name,
            state,
            commands,
            cumulative_water,
            cumulative_dose);

        cumulative_water += commands.water_liters;
        const std::array<std::pair<ControlledVariable, double>, 4>
            delivered_doses{{
                {ControlledVariable::PH, std::abs(commands.ph_milliliters)},
                {ControlledVariable::NITROGEN,
                 commands.nitrogen_milliliters},
                {ControlledVariable::PHOSPHORUS,
                 commands.phosphorus_milliliters},
                {ControlledVariable::POTASSIUM,
                 commands.potassium_milliliters},
            }};
        for (const auto& delivered : delivered_doses) {
            const auto index = index_of(delivered.first);
            seconds_since_last_dose[index] += kStepSeconds;
            if (delivered.second > 0.0) {
                cumulative_dose[index] += delivered.second;
                daily_dose[index] += delivered.second;
                seconds_since_last_dose[index] = 0.0;
            }
        }
        update_process(state, commands, current_hour);
    }
    csv.close();
    if (!csv) {
        throw std::runtime_error("errore durante la scrittura del CSV");
    }

    LiveRecipePlot graph;
    graph.show(csv_path);
    std::cout
        << "Ricetta: " << control_system.recipe().id << '\n'
        << "Fase simulata: "
        << control_system.recipe().phases.front().name << " ("
        << duration_hours << " h)\n"
        << "CSV: " << std::filesystem::absolute(csv_path) << '\n'
        << "Nessun PNG e stato creato.\n"
        << "Grafico aperto: premi Invio nel terminale per terminare.";
    std::string line;
    std::getline(std::cin, line);
    std::cout << "\nExperiment terminato.\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    if (!gnuplot_available()) {
        std::cerr
            << "Errore: gnuplot non e disponibile. Installalo per usare "
               "l'experiment interattivo delle ricette.\n";
        return 1;
    }

    try {
        const std::filesystem::path output_directory =
            argc > 1
                ? std::filesystem::path(argv[1])
                : std::filesystem::path("experiment_results");
        run_experiment(output_directory);
        return 0;
    } catch (const std::exception& error) {
        std::cerr
            << "recipe_control_simulation: " << error.what() << '\n';
        return 1;
    }
}
