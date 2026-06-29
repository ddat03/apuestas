# Ejecuta este script UNA VEZ como Administrador para programar la tarea diaria
# Click derecho → "Ejecutar como administrador"

$pythonPath = "C:\Users\diego\Desktop\APUESTAS\deportes_bot\venv\Scripts\python.exe"
$scriptPath = "C:\Users\diego\Desktop\APUESTAS\deportes_bot\main.py"
$workDir    = "C:\Users\diego\Desktop\APUESTAS\deportes_bot"

$action  = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "main.py --mode predict" `
    -WorkingDirectory $workDir

# Corre todos los días a las 9:00 AM
$trigger = New-ScheduledTaskTrigger -Daily -At "9:00AM"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -StartWhenAvailable `          # Si la PC estaba apagada, corre al encenderse
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName   "ApuestasDeportivas" `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -RunLevel   Highest `
    -Description "Análisis diario de apuestas deportivas" `
    -Force

Write-Host ""
Write-Host "✅ Tarea programada correctamente"
Write-Host "   Corre todos los días a las 9:00 AM"
Write-Host "   Si la PC estaba apagada, corre al encenderse"
Write-Host ""
Write-Host "Para ver la tarea: Busca 'Programador de tareas' en el menú inicio"
Write-Host "Para eliminarla:   Unregister-ScheduledTask -TaskName 'ApuestasDeportivas'"
