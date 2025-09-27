import pandas as pd
import re
from io import StringIO
from datetime import datetime

def parse_docker_stats_log(log_file_path):
    with open(log_file_path, 'r') as f:
        log_content = f.read()

    # Expressão regular para encontrar blocos de dados
    # Captura o timestamp e o bloco de stats até o próximo timestamp ou o delimitador de fim de amostra
    pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\n(?:CONTAINER ID.*?\n)?(.*?)(?=\n--- END OF SAMPLE|\Z)", re.DOTALL)

    all_data = []

    for match in pattern.finditer(log_content):
        timestamp_str = match.group(1)
        stats_block = match.group(2).strip()

        if not stats_block:
            continue

        # Tentativa de parseamento robusto para a saída tabular do docker stats
        # A saída de docker stats tem múltiplos espaços e pode ter strings complexas
        # Dividimos a linha em um número fixo de partes conhecidas e depois parseamos individualmente
        lines = stats_block.split('\n')
        for line in lines:
            line = line.strip()
            if not line: continue

            # Regex para dividir a linha em campos, lidando com espaços variáveis e unidades/símbolos
            # Grupo 1: CONTAINER ID
            # Grupo 2: NAME
            # Grupo 3: CPU %
            # Grupo 4: MEM USAGE
            # Grupo 5: MEM LIMIT
            # Grupo 6: MEM %
            # Grupo 7: NET I/O
            # Grupo 8: BLOCK I/O
            # Grupo 9: PIDS
            
            # Note: A regex para MEM USAGE / LIMIT, NET I/O, BLOCK I/O é crucial.
            # Ela precisa capturar tudo até o próximo espaço grande que separa as colunas.
            # Essa regex é mais complexa devido à variação do docker stats.
            # Vamos tentar uma mais flexível ou parsear o bloco inteiro e depois dividir por espaços grandes.

            # Re-tentando o parse com regex para cada linha de stats
            # Captura: (ID) (NAME) (CPU%) (MEM_USAGE / MEM_LIMIT) (MEM%) (NET_IO) (BLOCK_IO) (PIDS)
            # A chave é capturar as partes de "MEM USAGE / LIMIT", "NET I/O" e "BLOCK I/O" como um grupo único
            # até o próximo delimitador de espaços grandes.
            
            # A saída do docker stats tem espaços inconsistentes, o que torna o re.split(r'\s\s+') melhor.
            # No entanto, "MEM USAGE / LIMIT" ou "NET I/O" podem ter espaços internos.
            # A melhor forma é capturar os campos chave e depois tratar os campos compostos.
            
            # A saída que você me deu é:
            # 155cd74b2452   medapp-app-1   0.05%     270.3MiB / 2.908GiB   9.08%     19.5kB / 21.5kB   0B / 0B     39
            
            # Vamos usar uma regex para capturar os grupos de interesse
            match_line = re.match(r'(\S+)\s+(\S+)\s+([\d.]+%)?\s+(\S+\s*/\s*\S+)\s+([\d.]+%)?\s+(\S+\s*/\s*\S+)\s+(\S+\s*/\s*\S+)\s+(\d+)', line)

            if match_line:
                container_id = match_line.group(1)
                container_name = match_line.group(2)
                cpu_percent_raw = match_line.group(3)
                mem_usage_limit_raw = match_line.group(4)
                mem_percent_raw = match_line.group(5)
                net_io_raw = match_line.group(6)
                block_io_raw = match_line.group(7)
                pids_raw = match_line.group(8)

                row_dict = {
                    'Timestamp': timestamp_str,
                    'Container_ID': container_id,
                    'Container_Name': container_name,
                    'CPU_Percent_Raw': cpu_percent_raw,
                    'Mem_Usage_Limit_Raw': mem_usage_limit_raw,
                    'Mem_Percent_Raw': mem_percent_raw,
                    'Net_IO_Raw': net_io_raw,
                    'Block_IO_Raw': block_io_raw,
                    'PIDS_Raw': pids_raw
                }
                all_data.append(row_dict)
            else:
                print(f"Warning: Line did not match expected format: {line}")


    df = pd.DataFrame(all_data)

    if df.empty:
        return pd.DataFrame()


    def parse_percentage(s):
        return float(str(s).replace('%', '')) if pd.notna(s) and isinstance(s, str) else None

    def convert_size_to_mib(val_str):
        if pd.isna(val_str) or not isinstance(val_str, str): return None
        val_str = val_str.strip().upper()
        if 'MIB' in val_str: return float(val_str.replace('MIB',''))
        if 'GIB' in val_str: return float(val_str.replace('GIB','')) * 1024
        if 'KIB' in val_str: return float(val_str.replace('KIB','')) / 1024
        if 'B' in val_str: return float(val_str.replace('B','')) / (1024 * 1024)
        return None

    def parse_mem_usage_limit(s):
        if pd.isna(s) or not isinstance(s, str): return None, None
        parts = s.split(' / ')
        if len(parts) != 2: return None, None
        usage = convert_size_to_mib(parts[0])
        limit = convert_size_to_mib(parts[1])
        return usage, limit

    def convert_io_size_to_mb(val_str):
        if pd.isna(val_str) or not isinstance(val_str, str): return None
        val_str = val_str.strip().upper()
        if 'KB' in val_str: return float(val_str.replace('KB','')) / 1024
        if 'MB' in val_str: return float(val_str.replace('MB',''))
        if 'GB' in val_str: return float(val_str.replace('GB','')) * 1024
        if 'B' in val_str: return float(val_str.replace('B','')) / (1024 * 1024)
        return None

    def parse_net_block_io(s):
        if pd.isna(s) or not isinstance(s, str): return None, None
        parts = s.split(' / ')
        if len(parts) != 2: return None, None
        rx = convert_io_size_to_mb(parts[0])
        tx = convert_io_size_to_mb(parts[1])
        return rx, tx

    # Aplica as funções de parseamento
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    df['CPU_Percent'] = df['CPU_Percent_Raw'].apply(parse_percentage)
    df['Mem_Percent'] = df['Mem_Percent_Raw'].apply(parse_percentage)

    # Processa 'Mem_Usage_Limit_Raw' para duas novas colunas
    df[['Mem_Usage_MiB', 'Mem_Limit_MiB']] = df['Mem_Usage_Limit_Raw'].apply(lambda x: pd.Series(parse_mem_usage_limit(x)))
    
    # Processa Net_IO_Raw e Block_IO_Raw
    df[['Net_Rx_MB', 'Net_Tx_MB']] = df['Net_IO_Raw'].apply(lambda x: pd.Series(parse_net_block_io(x)))
    df[['Block_Read_MB', 'Block_Write_MB']] = df['Block_IO_Raw'].apply(lambda x: pd.Series(parse_net_block_io(x)))
    
    # Converte PIDS para numérico, tratando erros
    df['PIDS'] = pd.to_numeric(df['PIDS_Raw'], errors='coerce')


    # Seleciona e reordena as colunas finais
    final_cols = [
        'Timestamp', 'Container_ID', 'Container_Name', 'CPU_Percent',
        'Mem_Usage_MiB', 'Mem_Limit_MiB', 'Mem_Percent',
        'Net_Rx_MB', 'Net_Tx_MB', 'Block_Read_MB', 'Block_Write_MB', 'PIDS'
    ]
    df_final = df[final_cols].copy()

    return df_final


if __name__ == "__main__":
    log_file_java = 'docker_stats_raw_java.log'
    log_file_python = 'docker_stats_raw_python.log'

    df_parsed_java = parse_docker_stats_log(log_file_java)
    df_parsed_python = parse_docker_stats_log(log_file_python)

    if not df_parsed_java.empty:
        df_parsed_java.to_csv('all_docker_stats_parsed_java.csv', index=False)

        for name, group_df in df_parsed_java.groupby('Container_Name'):
            if(name == 'medapp-app-1'):
                name = 'java'
            if(name == 'postgres'):
                name = 'jPostgres'
            group_df.to_csv(f'{name}_parsed_stats.csv', index=False)
            print(f"Dados para {name} salvos em {name}_parsed_stats.csv")
        print("Processamento concluído1. Verifique os arquivos CSV gerados.")
    else:
        print("Nenhum dado válido foi parseado do log1.")


    if not df_parsed_python.empty:
        df_parsed_python.to_csv('all_docker_stats_parsed_python.csv', index=False)

        for name, group_df in df_parsed_python.groupby('Container_Name'):

            if(name == 'pymedapp'):
                name = 'python'
            if(name == 'postgres_db'):
                name = 'pPostgres'
                
            group_df.to_csv(f'{name}_parsed_stats.csv', index=False)
            print(f"Dados para {name} salvos em {name}_parsed_stats.csv")
        print("Processamento concluído2. Verifique os arquivos CSV gerados.")
    else:
        print("Nenhum dado válido foi parseado do log2.")