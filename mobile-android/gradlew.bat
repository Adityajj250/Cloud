@ECHO OFF
SETLOCAL
SET APP_HOME=%~dp0
IF NOT DEFINED ANDROID_HOME IF EXIST "%LOCALAPPDATA%\Android\Sdk" SET ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk
SET CLASSPATH=%APP_HOME%gradle\wrapper\gradle-wrapper.jar
java -classpath "%CLASSPATH%" org.gradle.wrapper.GradleWrapperMain %*
ENDLOCAL
